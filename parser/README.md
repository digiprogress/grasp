# parser

The tree-sitter code parser subcomponent of Grasp. See the [top-level
README](../README.md) for the full product; this file is just internal
notes on the parser module.

## Supported Languages

19 languages enabled. Vue/Svelte/Astro are preprocessed to TSX.

| Language | Imports | Classes | Interfaces | Functions | Methods | Enums | Inheritance |
|----------|---------|---------|------------|-----------|---------|-------|-------------|
| TypeScript | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| TSX | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| JavaScript | Yes | Yes | — | Yes | Yes | — | Yes |
| Python | Yes | Yes | — | Yes | Yes | — | Yes |
| Go | Yes | struct | interface | Yes | Yes (receiver) | — | — |
| Rust | Yes | struct | trait | Yes | Yes (impl) | Yes | Yes (impl Trait for) |
| Java | Yes | Yes | Yes | — | Yes | Yes | Yes |
| C# | Yes | Yes | Yes | — | Yes | Yes | Yes |
| Kotlin | Yes | Yes + object | Yes | Yes | Yes | Yes | Yes |
| Scala | Yes | Yes + object | trait | Yes | Yes | — | Yes |
| Dart | Yes | Yes | — | Yes | Yes | Yes | Yes |
| Swift | Yes | Yes + struct | protocol | Yes | Yes | Yes | Yes |
| PHP | Yes | Yes + trait | Yes | Yes | Yes | Yes | Yes |
| Ruby | — (require) | Yes + module | — | — | Yes | — | Yes |
| C | Yes | — | — | Yes | — | Yes | — |
| C++ | Yes | Yes + struct | — | Yes | Yes | Yes | Yes |

## Pipeline

```
Source files → Tree-sitter parse → Query capture → Extract → Resolve imports → ArcadeDB
```

- `modules/parser/queries.py` — tree-sitter queries per language
- `modules/parser/query_extractor.py` — captures → structured data
- `modules/parser/import_extractors.py` — language-specific import parsing
- `modules/resolver/` — cross-file reference resolution

## Limits

- `MAX_FILES = 10000` per project
- Per-file parse timeout: 60 s

## Testing

```
python3 -m pytest tests/ -v
```
