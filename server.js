const path = require("path");
const fs = require("fs");
const { spawnSync } = require("child_process");
const express = require("express");
const { ApolloServer } = require("@apollo/server");
const { expressMiddleware } = require("@apollo/server/express4");

const PORT = process.env.PORT || 4000;

// Load schema.graphql once so the UI can fetch the exact same file.
const schemaPath = path.join(__dirname, "schema.graphql");
const typeDefs = fs.readFileSync(schemaPath, "utf8");

// Serve schema.txt verbatim for the browser display.
const schemaTxtPath = path.join(__dirname, "schema.txt");
const schemaTxtText = fs.readFileSync(schemaTxtPath, "utf8");

// Simple in-memory store for demo purposes.
let items = ["apple", "banana"];

async function startServer() {
  const app = express();

  // Serve the browser UI.
  app.use(express.static(path.join(__dirname, "public")));

  // Apollo's Express middleware expects JSON bodies (GraphQL requests).
  app.use(express.json());

  // UI requirement: browser fetches one schema.graphql and displays it.
  app.get("/schema.graphql", (req, res) => {
    res.type("text/plain").send(typeDefs);
  });

  app.get("/schema.txt", (req, res) => {
    res.type("text/plain").send(schemaTxtText);
  });

  // UI requirement: when dropdown=delete, send an arbitrary field name here.
  // This endpoint uses the Python analysis script and returns JSON.
  app.get("/api/delete-check", (req, res) => {
    const field = (req.query.field || "").toString().trim();
    if (!field) {
      res.status(400).json({ error: "Missing query param: field" });
      return;
    }

    const schemaFile = "schema.txt";
    const scriptPath = path.join(__dirname, "delete_dependency_check.py");
    const schemaPathLocal = path.join(__dirname, schemaFile);

    const proc = spawnSync(
      "python3",
      [
        scriptPath,
        "--schema",
        schemaPathLocal,
        "--field",
        field,
        "--json",
      ],
      { encoding: "utf8" }
    );

    if (proc.error) {
      res.status(500).json({ error: proc.error.message });
      return;
    }

    const stdout = (proc.stdout || "").trim();
    if (!stdout) {
      res.status(proc.status || 500).json({
        error: "Python script returned no output",
        stderr: proc.stderr,
      });
      return;
    }

    try {
      const data = JSON.parse(stdout);
      res.status(proc.status === 0 ? 200 : 400).json(data);
    } catch (e) {
      res.status(500).json({
        error: "Failed to parse JSON from python script",
        raw: stdout,
        stderr: proc.stderr,
      });
    }
  });

  const server = new ApolloServer({
    typeDefs,
    resolvers: {
      Query: {
        items: () => items,
      },
      Mutation: {
        insert: (_, { value }) => {
          items = [...items, value];
          return items;
        },
        delete: (_, { index }) => {
          const i = Number(index);
          if (!Number.isInteger(i)) {
            throw new Error("index must be an integer");
          }
          if (i < 0 || i >= items.length) {
            throw new Error(`index out of range (0..${items.length - 1})`);
          }
          items = items.filter((_, idx) => idx !== i);
          return items;
        },
      },
    },
  });

  await server.start();

  app.use(
    "/graphql",
    expressMiddleware(server, {
      // Keep defaults; Apollo parses JSON bodies.
    })
  );

  app.listen(PORT, () => {
    // eslint-disable-next-line no-console
    console.log(`Server running at http://localhost:${PORT}`);
    // eslint-disable-next-line no-console
    console.log(`UI at http://localhost:${PORT}/`);
    // eslint-disable-next-line no-console
    console.log(`GraphQL at http://localhost:${PORT}/graphql`);
  });
}

startServer().catch((err) => {
  // eslint-disable-next-line no-console
  console.error(err);
  process.exit(1);
});

