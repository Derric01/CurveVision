# CurveVision Web

The CurveVision web application and annotation editor.

```bash
npm install
npm run dev          # http://localhost:5173, proxying /api to localhost:8000
npm run test         # engine unit tests
npm run bench        # annotation-engine benchmarks
npm run build        # typecheck + production build
```

## Layout

```
src/
├─ canvas/      the annotation engine — plain TypeScript, imports nothing from React
│  ├─ scene.ts      annotations, labels, selection
│  ├─ spatial.ts    R-tree index (rbush): viewport culling and O(log n) picking
│  ├─ renderer.ts   layered Canvas2D painting
│  ├─ tools.ts      one state machine per interaction
│  ├─ commands.ts   undo/redo with drag coalescing
│  └─ engine.ts     the facade React mounts
├─ features/    route-level modules (auth, projects, tasks, editor)
├─ api/         typed client for /api/v1
└─ ui/          the CurveVision design-system primitives
```

The engine is framework-agnostic on purpose: it is the most performance-sensitive surface
in the product, it must be testable in Node, and keeping React out of it is what stops it
becoming an unmaintainable component. See
[ADR 0003](../docs/adr/0003-canvas2d-with-spatial-index.md) for why it uses Canvas2D and a
spatial index rather than an SVG DOM.

## License

MIT — see [LICENSE](../LICENSE).
