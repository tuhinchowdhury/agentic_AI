# Node.js + GraphQL (Docker)

## Run with Docker

```sh
docker compose up --build
```

Then open:
- UI: `http://localhost:4000/`
- GraphQL: `http://localhost:4000/graphql`

## Notes

The UI reads `schema.graphql` from `GET /schema.graphql` and displays it in a textarea.

Mutation input meaning:
- `insert` uses the input as `value` (string)
- `delete` uses the input as `index` (integer)

