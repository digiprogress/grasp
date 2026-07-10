#!/usr/bin/env bun
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js'
import { readdir, readFile, writeFile, mkdir, rm } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { join, dirname, resolve as pathResolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import * as yaml from 'js-yaml'

const URL = process.env.ARCADEDB_URL ?? 'http://localhost:2480'
const DB = process.env.ARCADEDB_DATABASE ?? 'grasp'
const USER = process.env.ARCADEDB_USER ?? 'root'
const PASS = process.env.ARCADEDB_ROOT_PASSWORD ?? ''
const PARSER_URL = process.env.PARSER_URL ?? 'http://127.0.0.1:3334'
const OPENAI_API_KEY = process.env.OPENAI_API_KEY ?? ''

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)
// Patterns live under /root/grasp/patterns/<pack>/<name>.md (Fabric shape).
const PATTERNS_ROOT =
  process.env.PATTERNS_ROOT ?? pathResolve(__dirname, '../../../patterns')

const EMBEDDING_MODEL = 'text-embedding-3-small'
const MAX_TOKENS_PER_CHUNK = 800
const APPROX_CHARS_PER_TOKEN = 4

// Fabric-shape strategy library (small subset). Stays inline for now — later
// we can move to /patterns/strategies/*.json.
const STRATEGIES: Record<string, string> = {
  cot: 'Think step by step to answer the question. Return the final answer in the required format.',
  cod: 'Think step by step, keeping a minimal draft (5 words max) for each step. Return the final answer in the required format.',
  tot: 'Explore multiple lines of reasoning in parallel. Evaluate each. Select the best. Return the final answer.',
  aot: 'Break the problem into smallest independent atomic sub-problems. Solve each. Compose the final answer.',
  ltm: 'Solve from easiest sub-problem to hardest. Use earlier answers to build later ones. Return the final answer.',
  'self-consistent':
    'Independently produce three reasoning paths. Return the answer that is most consistent across them.',
  'self-refine':
    'Answer. Critique your answer. Refine. Return the refined answer only.',
  reflexion:
    'Answer. Briefly critique. Provide a refined answer that addresses the critique.',
  standard: '',
}

if (!PASS) {
  console.error('ARCADEDB_ROOT_PASSWORD not set')
  process.exit(1)
}

// ─── helpers: embeddings + arcade ────────────────────────────────────────
function approxTokens(s: string): number {
  return Math.ceil(s.length / APPROX_CHARS_PER_TOKEN)
}

function splitSentences(text: string): string[] {
  return text
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

function chunkText(text: string): string[] {
  const paragraphs = text
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0)

  const chunks: string[] = []
  for (const para of paragraphs) {
    if (approxTokens(para) <= MAX_TOKENS_PER_CHUNK) {
      chunks.push(para)
      continue
    }
    const sentences = splitSentences(para)
    let buf: string[] = []
    let bufTokens = 0
    for (const s of sentences) {
      const t = approxTokens(s)
      if (bufTokens + t > MAX_TOKENS_PER_CHUNK && buf.length > 0) {
        chunks.push(buf.join(' '))
        buf = []
        bufTokens = 0
      }
      if (t > MAX_TOKENS_PER_CHUNK) {
        const charLimit = MAX_TOKENS_PER_CHUNK * APPROX_CHARS_PER_TOKEN
        for (let i = 0; i < s.length; i += charLimit) {
          chunks.push(s.slice(i, i + charLimit))
        }
        continue
      }
      buf.push(s)
      bufTokens += t
    }
    if (buf.length > 0) chunks.push(buf.join(' '))
  }
  return chunks.length > 0 ? chunks : [text]
}

async function embed(text: string): Promise<number[]> {
  if (!OPENAI_API_KEY) {
    throw new Error('OPENAI_API_KEY not set — required for embeddings')
  }
  const res = await fetch('https://api.openai.com/v1/embeddings', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${OPENAI_API_KEY}`,
    },
    body: JSON.stringify({ input: text, model: EMBEDDING_MODEL }),
  })
  if (!res.ok) {
    throw new Error(`OpenAI embeddings ${res.status}: ${await res.text()}`)
  }
  const data = (await res.json()) as { data: { embedding: number[] }[] }
  return data.data[0].embedding
}

function cosineSim(a: number[], b: number[]): number {
  let dot = 0
  let na = 0
  let nb = 0
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i]
    na += a[i] * a[i]
    nb += b[i] * b[i]
  }
  return dot / (Math.sqrt(na) * Math.sqrt(nb) + 1e-12)
}

const authHeader = 'Basic ' + Buffer.from(`${USER}:${PASS}`).toString('base64')

async function arcade(
  endpoint: 'query' | 'command',
  language: 'sql' | 'sqlscript' | 'cypher' | 'gremlin',
  text: string,
  params?: Record<string, unknown>,
  database?: string,
) {
  const body: Record<string, unknown> = { language, command: text }
  if (params) body.params = params

  const db = database ?? DB
  const res = await fetch(`${URL}/api/v1/${endpoint}/${db}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: authHeader,
    },
    body: JSON.stringify(body),
  })
  const text2 = await res.text()
  if (!res.ok) {
    throw new Error(`ArcadeDB ${endpoint} [${db}] ${res.status}: ${text2}`)
  }
  return JSON.parse(text2)
}

async function listDatabases(): Promise<string[]> {
  const res = await fetch(`${URL}/api/v1/databases`, {
    headers: { Authorization: authHeader },
  })
  if (!res.ok) {
    throw new Error(`ArcadeDB /databases ${res.status}: ${await res.text()}`)
  }
  const data = (await res.json()) as { result: string[] }
  return data.result ?? []
}

// ─── patterns (flat, fabric-shape) ───────────────────────────────────────
interface Frontmatter {
  name?: string
  description?: string
  category?: string
  source?: string
  license?: string
  lang?: string
  [k: string]: unknown
}

interface PatternSummary {
  name: string
  description?: string
  category?: string
  source?: string
  lang?: string
  file: string
}

function parseFrontmatter(md: string): { meta: Frontmatter; body: string } {
  if (!md.startsWith('---')) return { meta: {}, body: md }
  const end = md.indexOf('\n---', 3)
  if (end === -1) return { meta: {}, body: md }
  const rawMeta = md.slice(3, end).trim()
  const body = md.slice(end + 4).replace(/^\n+/, '')
  try {
    const meta = (yaml.load(rawMeta) as Frontmatter) ?? {}
    return { meta, body }
  } catch {
    return { meta: {}, body }
  }
}

function safeSeg(seg: string): string {
  if (!seg || seg.includes('..') || seg.includes('/') || seg.includes('\\')) {
    throw new Error(`Unsafe path segment: ${seg}`)
  }
  return seg
}

function patternPath(name: string): string {
  safeSeg(name)
  return join(PATTERNS_ROOT, name + '.md')
}

async function listPatterns(): Promise<PatternSummary[]> {
  if (!existsSync(PATTERNS_ROOT)) return []
  const files = (await readdir(PATTERNS_ROOT)).filter((f) => f.endsWith('.md'))
  const out: PatternSummary[] = []
  for (const f of files) {
    const full = join(PATTERNS_ROOT, f)
    const raw = await readFile(full, 'utf-8')
    const { meta } = parseFrontmatter(raw)
    out.push({
      name: (meta.name as string) ?? f.replace(/\.md$/, ''),
      description: meta.description as string | undefined,
      category: meta.category as string | undefined,
      source: meta.source as string | undefined,
      lang: meta.lang as string | undefined,
      file: full,
    })
  }
  return out.sort((a, b) => a.name.localeCompare(b.name))
}

async function findPattern(name: string): Promise<PatternSummary | null> {
  const all = await listPatterns()
  return all.find((p) => p.name === name) ?? null
}

function interpolate(body: string, inputs: Record<string, unknown>): string {
  return body.replace(/\{\{\s*([\w.]+)\s*\}\}/g, (_, key: string) => {
    const val = key.split('.').reduce<any>(
      (acc, part) => (acc == null ? acc : acc[part]),
      inputs,
    )
    return val == null ? `{{${key}}}` : String(val)
  })
}

/**
 * Compose a pattern's system prompt + optional strategy + user input into a
 * single string, in the fabric spirit (system.md + --strategy + stdin).
 */
async function composePattern(
  pattern: PatternSummary,
  opts: { strategy?: string; input?: string; variables?: Record<string, unknown> },
): Promise<string> {
  const raw = await readFile(pattern.file, 'utf-8')
  const { body } = parseFrontmatter(raw)
  const interpolated = interpolate(body, opts.variables ?? {})

  const parts: string[] = [interpolated.trim()]

  if (opts.strategy) {
    const key = opts.strategy.toLowerCase()
    const strategyPrompt = STRATEGIES[key]
    if (strategyPrompt == null) {
      throw new Error(
        `Unknown strategy: ${opts.strategy}. Known: ${Object.keys(STRATEGIES).join(', ')}`,
      )
    }
    if (strategyPrompt) parts.push(`\n# STRATEGY\n${strategyPrompt}`)
  }

  if (opts.input) {
    parts.push(`\n# INPUT\n${opts.input.trim()}`)
  }

  return parts.join('\n\n').trimEnd() + '\n'
}

// ─── MCP server + tools ──────────────────────────────────────────────────
const server = new Server(
  { name: 'grasp-graph', version: '0.2.0' },
  { capabilities: { tools: {} } },
)

const DB_DESCRIPTION =
  `Optional database name. Defaults to the primary "${DB}" database. ` +
  `Use list_projects to see all parsed repos.`

const tools = [
  // ── Graph ─────────────────────────────────────────────────────────────
  {
    name: 'query',
    description:
      'Run a read-only SQL/Cypher/Gremlin query. Use SELECT statements only. For writes use the execute tool.',
    inputSchema: {
      type: 'object',
      properties: {
        language: { type: 'string', enum: ['sql', 'cypher', 'gremlin'], default: 'sql' },
        text: { type: 'string', description: 'Query text' },
        params: { type: 'object', description: 'Optional named parameters' },
        database: { type: 'string', description: DB_DESCRIPTION },
      },
      required: ['text'],
    },
  },
  {
    name: 'execute',
    description:
      'Execute a write command (INSERT, UPDATE, DELETE, CREATE, DROP). Returns the result.',
    inputSchema: {
      type: 'object',
      properties: {
        language: {
          type: 'string',
          enum: ['sql', 'sqlscript', 'cypher', 'gremlin'],
          default: 'sql',
        },
        text: { type: 'string', description: 'Command text' },
        params: { type: 'object', description: 'Optional named parameters' },
        database: { type: 'string', description: DB_DESCRIPTION },
      },
      required: ['text'],
    },
  },
  {
    name: 'create_vertex',
    description:
      'Create a vertex of a given type with properties. Returns the created record (including @rid).',
    inputSchema: {
      type: 'object',
      properties: {
        type: { type: 'string', description: 'Vertex type, e.g. Concept, Domain, Entity' },
        properties: { type: 'object' },
        database: { type: 'string', description: DB_DESCRIPTION },
      },
      required: ['type', 'properties'],
    },
  },
  {
    name: 'create_edge',
    description:
      'Create an edge between two vertices. From/to can be @rids or SQL expressions selecting a single vertex.',
    inputSchema: {
      type: 'object',
      properties: {
        type: { type: 'string' },
        from: { type: 'string' },
        to: { type: 'string' },
        properties: { type: 'object' },
        database: { type: 'string', description: DB_DESCRIPTION },
      },
      required: ['type', 'from', 'to'],
    },
  },
  {
    name: 'get_schema',
    description: 'List all vertex types, edge types, and their properties in the graph.',
    inputSchema: {
      type: 'object',
      properties: {
        database: { type: 'string', description: DB_DESCRIPTION },
      },
    },
  },

  // ── Parse hemisphere ──────────────────────────────────────────────────
  {
    name: 'parse_repo',
    description:
      'Trigger the Grasp code parser on a public GitHub repo. Writes file/function/class skeleton + imports/inheritance edges into an ArcadeDB database named after the project. Returns arcadedb_stats.',
    inputSchema: {
      type: 'object',
      properties: {
        repo: { type: 'string', description: 'GitHub URL' },
        project: { type: 'string' },
        clean: { type: 'boolean', default: false },
      },
      required: ['repo'],
    },
  },
  {
    name: 'list_projects',
    description:
      `List all databases available in the graph — the primary "${DB}" plus every parsed repo.`,
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'describe_project',
    description:
      'Summarize a parsed project database — vertex/edge counts by type, sample top-level directories, entry files.',
    inputSchema: {
      type: 'object',
      properties: { project: { type: 'string' } },
      required: ['project'],
    },
  },

  // ── Patterns hemisphere (fabric-shape) ────────────────────────────────
  {
    name: 'list_patterns',
    description:
      'List all patterns in the Grasp catalog across packs. Every pattern is a single .md file with YAML frontmatter (name, description, category, source, license). Use this to discover what patterns are runnable.',
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'read_pattern',
    description:
      'Read a pattern .md file. Returns { name, frontmatter, body }. Use before edit or before running by hand.',
    inputSchema: {
      type: 'object',
      properties: {
        name: { type: 'string', description: 'Pattern name without .md extension' },
      },
      required: ['name'],
    },
  },
  {
    name: 'write_pattern',
    description:
      'Create or overwrite a pattern .md file. Writes YAML frontmatter + body. Idempotent.',
    inputSchema: {
      type: 'object',
      properties: {
        name: { type: 'string' },
        frontmatter: { type: 'object' },
        body: { type: 'string' },
      },
      required: ['name', 'body'],
    },
  },
  {
    name: 'delete_pattern',
    description: 'Delete a pattern .md file.',
    inputSchema: {
      type: 'object',
      properties: {
        name: { type: 'string' },
      },
      required: ['name'],
    },
  },
  {
    name: 'run_pattern',
    description:
      'Assemble a fabric-style composed prompt from a pattern + optional strategy + optional input + optional variables. Returns the composed prompt as the tool result — Grasp does NOT call an LLM. The calling agent executes the composed prompt in its next turn (Anthropic Skills paradigm).',
    inputSchema: {
      type: 'object',
      properties: {
        name: {
          type: 'string',
          description: 'Pattern name (basename without .md).',
        },
        input: {
          type: 'string',
          description: 'The user input to run the pattern on (e.g. article text, transcript, URL contents).',
        },
        strategy: {
          type: 'string',
          description:
            'Optional reasoning strategy. Known: cot, cod, tot, aot, ltm, self-consistent, self-refine, reflexion, standard.',
        },
        variables: {
          type: 'object',
          description: 'Optional {{name}} placeholder values.',
        },
      },
      required: ['name'],
    },
  },

  // ── Embeddings / semantic search ──────────────────────────────────────
  {
    name: 'embed_annotation',
    description:
      "Chunk an Annotation's content and create Chunk vertices with OpenAI embeddings. Links Chunk → Annotation via HAS_CHUNK. Paragraph-first, sentence-fallback if a paragraph exceeds ~800 tokens. Idempotent.",
    inputSchema: {
      type: 'object',
      properties: {
        annotation_id: { type: 'string' },
      },
      required: ['annotation_id'],
    },
  },
  {
    name: 'semantic_search',
    description:
      'Cosine-similarity search over Chunk embeddings. Returns top-K chunks with their parent Annotation.',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string' },
        top_k: { type: 'number', default: 5 },
      },
      required: ['query'],
    },
  },
]

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools }))

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: args = {} } = req.params

  try {
    switch (name) {
      // ── Graph ─────────────────────────────────────────────────────────
      case 'query': {
        const result = await arcade(
          'query',
          (args.language as any) ?? 'sql',
          args.text as string,
          args.params as Record<string, unknown> | undefined,
          args.database as string | undefined,
        )
        return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
      }
      case 'execute': {
        const result = await arcade(
          'command',
          (args.language as any) ?? 'sql',
          args.text as string,
          args.params as Record<string, unknown> | undefined,
          args.database as string | undefined,
        )
        return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
      }
      case 'create_vertex': {
        const type = args.type as string
        const props = args.properties as Record<string, unknown>
        const database = args.database as string | undefined
        const setClauses = Object.entries(props)
          .map(([k]) => `${k} = :${k}`)
          .join(', ')
        const sql = `CREATE VERTEX ${type} SET ${setClauses}`
        const result = await arcade('command', 'sql', sql, props, database)
        return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
      }
      case 'create_edge': {
        const type = args.type as string
        const from = args.from as string
        const to = args.to as string
        const props = (args.properties as Record<string, unknown>) ?? {}
        const database = args.database as string | undefined
        const fromExpr = from.startsWith('#') ? from : `(${from})`
        const toExpr = to.startsWith('#') ? to : `(${to})`
        let sql = `CREATE EDGE ${type} FROM ${fromExpr} TO ${toExpr}`
        if (Object.keys(props).length > 0) {
          const setClauses = Object.entries(props).map(([k]) => `${k} = :${k}`).join(', ')
          sql += ` SET ${setClauses}`
        }
        const result = await arcade('command', 'sql', sql, props, database)
        return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
      }
      case 'get_schema': {
        const database = args.database as string | undefined
        const result = await arcade(
          'query',
          'sql',
          'SELECT FROM schema:types',
          undefined,
          database,
        )
        return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
      }

      // ── Parse ─────────────────────────────────────────────────────────
      case 'parse_repo': {
        const res = await fetch(`${PARSER_URL}/parse`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            repo: args.repo,
            project: args.project,
            clean: args.clean ?? false,
          }),
        })
        const body = await res.text()
        if (!res.ok) throw new Error(`Parser ${res.status}: ${body}`)
        return { content: [{ type: 'text', text: body }] }
      }
      case 'list_projects': {
        const dbs = await listDatabases()
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({ primary: DB, databases: dbs.sort() }, null, 2),
            },
          ],
        }
      }
      case 'describe_project': {
        const project = args.project as string
        const dbs = await listDatabases()
        if (!dbs.includes(project)) {
          throw new Error(`Unknown project: ${project}. Known: ${dbs.join(', ')}`)
        }
        const types = (await arcade(
          'query',
          'sql',
          'SELECT name, type, records FROM schema:types',
          undefined,
          project,
        )) as { result: { name: string; type: string; records: number }[] }
        const vertexCounts: Record<string, number> = {}
        const edgeCounts: Record<string, number> = {}
        for (const t of types.result ?? []) {
          if (t.type === 'vertex') vertexCounts[t.name] = t.records
          else if (t.type === 'edge') edgeCounts[t.name] = t.records
        }
        const summary: Record<string, unknown> = {
          project,
          vertices: vertexCounts,
          edges: edgeCounts,
        }
        try {
          const topDirs = (await arcade(
            'query',
            'sql',
            'SELECT path FROM Directory WHERE depth = 1 ORDER BY path LIMIT 20',
            undefined,
            project,
          )) as { result: { path: string }[] }
          if (topDirs.result?.length) summary.top_directories = topDirs.result.map((r) => r.path)
          const entryFiles = (await arcade(
            'query',
            'sql',
            "SELECT path FROM File WHERE name IN ['index.ts','index.js','main.py','main.rs','main.go','lib.rs'] LIMIT 20",
            undefined,
            project,
          )) as { result: { path: string }[] }
          if (entryFiles.result?.length) summary.entry_files = entryFiles.result.map((r) => r.path)
        } catch {}
        return { content: [{ type: 'text', text: JSON.stringify(summary, null, 2) }] }
      }

      // ── Patterns ──────────────────────────────────────────────────────
      case 'list_patterns': {
        const patterns = await listPatterns()
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  patterns_root: PATTERNS_ROOT,
                  count: patterns.length,
                  patterns: patterns.map((p) => ({
                    name: p.name,
                    description: p.description,
                    category: p.category,
                    source: p.source,
                  })),
                },
                null,
                2,
              ),
            },
          ],
        }
      }
      case 'read_pattern': {
        const name = args.name as string
        const path = patternPath(name)
        if (!existsSync(path)) throw new Error(`Not found: ${path}`)
        const raw = await readFile(path, 'utf-8')
        const { meta, body } = parseFrontmatter(raw)
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({ name, file: path, frontmatter: meta, body }, null, 2),
            },
          ],
        }
      }
      case 'write_pattern': {
        const name = args.name as string
        const frontmatter = (args.frontmatter as Frontmatter) ?? {}
        const body = (args.body as string) ?? ''
        if (!frontmatter.name) frontmatter.name = name
        const path = patternPath(name)
        await mkdir(dirname(path), { recursive: true })
        const dumped = yaml.dump(frontmatter, { lineWidth: 100, noRefs: true }).trimEnd()
        const content = `---\n${dumped}\n---\n\n${body.trimEnd()}\n`
        await writeFile(path, content, 'utf-8')
        return {
          content: [
            { type: 'text', text: JSON.stringify({ written: path, bytes: content.length }, null, 2) },
          ],
        }
      }
      case 'delete_pattern': {
        const name = args.name as string
        const path = patternPath(name)
        if (!existsSync(path)) throw new Error(`Not found: ${path}`)
        await rm(path)
        return { content: [{ type: 'text', text: JSON.stringify({ deleted: path }, null, 2) }] }
      }
      case 'run_pattern': {
        const name = args.name as string
        const pattern = await findPattern(name)
        if (!pattern) throw new Error(`Pattern not found: ${name}`)
        const composed = await composePattern(pattern, {
          strategy: args.strategy as string | undefined,
          input: args.input as string | undefined,
          variables: args.variables as Record<string, unknown> | undefined,
        })
        return {
          content: [
            {
              type: 'text',
              text:
                `The following is a Grasp composed pattern (${pattern.name}). ` +
                `Execute it as if it were part of your own turn.\n\n──────── PROMPT ────────\n\n` +
                composed +
                `\n────────────────────────`,
            },
          ],
        }
      }

      // ── Embeddings / search ──────────────────────────────────────────
      case 'embed_annotation': {
        const annId = args.annotation_id as string
        const ann = (await arcade(
          'query',
          'sql',
          'SELECT @rid as rid, content FROM Annotation WHERE id = :id',
          { id: annId },
        )) as { result: { rid: string; content: string }[] }

        if (!ann.result || ann.result.length === 0) {
          throw new Error(`Annotation ${annId} not found`)
        }
        const annotation = ann.result[0]

        const existing = (await arcade(
          'query',
          'sql',
          `SELECT id FROM Chunk WHERE id LIKE :pattern`,
          { pattern: `chunk_${annId}_%` },
        )) as { result: { id: string }[] }
        if (existing.result && existing.result.length > 0) {
          return {
            content: [
              {
                type: 'text',
                text: JSON.stringify(
                  {
                    skipped: true,
                    reason: 'chunks already exist',
                    chunk_count: existing.result.length,
                  },
                  null,
                  2,
                ),
              },
            ],
          }
        }

        const chunks = chunkText(annotation.content)
        const created: string[] = []
        for (let i = 0; i < chunks.length; i++) {
          const text = chunks[i]
          const vec = await embed(text)
          const chunkId = `chunk_${annId}_${i.toString().padStart(3, '0')}`
          const now = new Date().toISOString()
          await arcade(
            'command',
            'sql',
            `CREATE VERTEX Chunk SET id = :id, text = :text, idx = :idx,
             embedding = :embedding, embedding_model = :model, created_at = :created_at`,
            {
              id: chunkId,
              text,
              idx: i,
              embedding: vec,
              model: EMBEDDING_MODEL,
              created_at: now,
            },
          )
          await arcade(
            'command',
            'sql',
            `CREATE EDGE HAS_CHUNK FROM (SELECT FROM Annotation WHERE id = :annId)
             TO (SELECT FROM Chunk WHERE id = :chunkId)`,
            { annId, chunkId },
          )
          created.push(chunkId)
        }

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  annotation_id: annId,
                  chunks_created: created.length,
                  chunk_ids: created,
                  embedding_model: EMBEDDING_MODEL,
                },
                null,
                2,
              ),
            },
          ],
        }
      }

      case 'semantic_search': {
        const query = args.query as string
        const topK = (args.top_k as number) ?? 5
        const queryVec = await embed(query)
        const all = (await arcade(
          'query',
          'sql',
          'SELECT id, text, idx, embedding FROM Chunk LIMIT 10000',
        )) as {
          result: { id: string; text: string; idx: number; embedding: number[] }[]
        }
        if (!all.result || all.result.length === 0) {
          return {
            content: [
              {
                type: 'text',
                text: JSON.stringify({ matches: [], note: 'no chunks indexed yet' }, null, 2),
              },
            ],
          }
        }
        const scored = all.result
          .filter((c) => Array.isArray(c.embedding) && c.embedding.length > 0)
          .map((c) => ({
            chunk_id: c.id,
            text: c.text,
            idx: c.idx,
            score: cosineSim(queryVec, c.embedding),
          }))
          .sort((a, b) => b.score - a.score)
          .slice(0, topK)

        const enriched = await Promise.all(
          scored.map(async (m) => {
            const annIdMatch = m.chunk_id.match(/^chunk_(.+)_\d+$/)
            const annId = annIdMatch ? annIdMatch[1] : null
            let parent: { id: string; summary?: string; annotation_type?: string } | null = null
            if (annId) {
              const r = (await arcade(
                'query',
                'sql',
                'SELECT id, summary, annotation_type FROM Annotation WHERE id = :id',
                { id: annId },
              )) as { result: any[] }
              if (r.result && r.result.length > 0) parent = r.result[0]
            }
            return { ...m, annotation: parent }
          }),
        )

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({ query, top_k: topK, matches: enriched }, null, 2),
            },
          ],
        }
      }

      default:
        return {
          content: [{ type: 'text', text: `Unknown tool: ${name}` }],
          isError: true,
        }
    }
  } catch (err) {
    return {
      content: [{ type: 'text', text: `Error: ${(err as Error).message}` }],
      isError: true,
    }
  }
})

const transport = new StdioServerTransport()
await server.connect(transport)
console.error('grasp-graph MCP server running on stdio')
