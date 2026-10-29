import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  catalogFamilies,
  discoverReviewTargets,
  executeReview,
  parseOptions,
  resolveOutputRoot,
  validateScenarioReport,
} from "./review.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const fixedTime = () => new Date("2026-10-23T01:02:03.004Z");
const fixedRepositoryState = () => ({ revision: "a".repeat(40), dirty: false });

async function testOutput(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "metropolis-security-test-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  return root;
}

async function currentFamilies() {
  const catalog = JSON.parse(
    await fs.readFile(path.join(repoRoot, "scripts/attacks/scenarios/v1.json"), "utf8"),
  );
  return catalogFamilies(catalog);
}

async function currentTargets() {
  return (await discoverReviewTargets(repoRoot)).targetPaths;
}

function scenarioOutput(families, targetPaths) {
  return {
    schema_version: "1.0",
    review_kind: "theoretical",
    generated_at_utc: "2026-10-23T01:02:04.000Z",
    scenarios: [...families.entries()].map(([family, catalogIds]) => ({
      family,
      catalog_ids: catalogIds,
      title: `Theoretical ${family} scenario`,
      initial_foothold: "A compromised host on a declared subnet",
      command_infrastructure: "An external controller, if reachable",
      potential_ot_impact: "Potential service disruption",
      prerequisites: ["Initial compromise"],
      steps: [
        {
          source: "compromised host",
          destination: "service host",
          protocol: "TCP",
          description: "Attempt a connection",
          path_status: "unknown",
          basis: "hypothetical",
          evidence_paths: [targetPaths[0]],
        },
      ],
      observables: ["Connection attempts"],
      mitigations: ["Review network policy"],
      limitations: ["No live GNS3 verification"],
      cves: [],
    })),
  };
}

function scenarioMarkdown(families) {
  return (
    "# Theoretical GNS3 scenarios — live behavior unverified\n\n" +
    "| Family | Foothold | Path and status | Possible impact | Evidence |\n" +
    "|---|---|---|---|---|\n" +
    [...families.keys()].map((family) => `| ${family} | Host | Unknown | Potential | Declared plan |`).join("\n") +
    "\n"
  );
}

function mockClient(run) {
  return {
    async preflight(_repository, options) {
      return { repository: _repository, target: { kind: "path" }, outputDir: options.outputDir };
    },
    run,
  };
}

test("CLI requires an explicit positive cost limit for a scan", () => {
  assert.throws(() => parseOptions([]), /requires --max-cost-usd/);
  assert.throws(() => parseOptions(["--max-cost-usd", "0"]), /positive number/);
  assert.equal(parseOptions(["--max-cost-usd", "7.5"]).maxCostUsd, 7.5);
  assert.equal(parseOptions(["--preflight"]).model, "gpt-6-sol");
  assert.equal(parseOptions(["--preflight", "--model", "gpt-6.1-sol"]).model, "gpt-6.1-sol");
  assert.equal(parseOptions(["--preflight"]).maxCostUsd, undefined);
  assert.equal(parseOptions(["--max-cost-usd", "2", "--auth", "api-key"]).auth, "api-key");
});

test("preflight does not start a scan or create result files", async (t) => {
  const outputRoot = await testOutput(t);
  const client = {
    async preflight(_repository, options) {
      return { target: { kind: "path" } };
    },
    async run() {
      assert.fail("preflight must not start a scan");
    },
  };
  const result = await executeReview(client, {
    repoRoot,
    options: parseOptions(["--preflight", "--output-root", outputRoot]),
    now: fixedTime,
    repositoryState: fixedRepositoryState,
  });
  assert.equal(result.status, "preflight");
  assert.equal(result.model, "gpt-6-sol");
  assert.equal(result.cyber_access_program, "daybreak_blue");
  assert.deepEqual(await fs.readdir(outputRoot), []);
});

test("failed scan preflight writes an incomplete review manifest", async (t) => {
  const outputRoot = await testOutput(t);
  const client = {
    async preflight() {
      throw new Error("Injected invalid target");
    },
    async run() {
      assert.fail("a failed preflight must not start a scan");
    },
  };
  const result = await executeReview(client, {
    repoRoot,
    options: parseOptions(["--max-cost-usd", "5", "--output-root", outputRoot]),
    now: fixedTime,
    repositoryState: fixedRepositoryState,
  });
  assert.equal(result.overall_status, "incomplete");
  assert.equal(result.scan.status, "failed");
  assert.equal(result.scenarios.status, "not_started");
  assert.equal(result.failures[0].phase, "preflight");
  assert.ok(await fs.stat(result.metadata_path));
});

test("completed run saves attribution, hashes, and separate validated scenarios", async (t) => {
  const outputRoot = await testOutput(t);
  const families = await currentFamilies();
  const targetPaths = await currentTargets();
  let scanOptions;
  const client = mockClient(async (_repository, options) => {
    scanOptions = options;
    const reviewDir = path.dirname(options.outputDir);
    await fs.writeFile(
      path.join(reviewDir, "scenarios.json"),
      JSON.stringify(scenarioOutput(families, targetPaths)),
    );
    await fs.writeFile(path.join(reviewDir, "scenarios.md"), scenarioMarkdown(families));
    return {
      manifest: { scan: { status: "completed" } },
      reportPath: path.join(options.outputDir, "report.md"),
      findingsPath: path.join(options.outputDir, "findings.json"),
      coveragePath: path.join(options.outputDir, "coverage.json"),
      manifestPath: path.join(options.outputDir, "scan-manifest.json"),
      pluginVersion: "test-plugin",
      coverage: { deferred: [] },
    };
  });
  const result = await executeReview(client, {
    repoRoot,
    options: parseOptions(["--max-cost-usd", "5", "--output-root", outputRoot]),
    now: fixedTime,
    repositoryState: fixedRepositoryState,
  });
  assert.equal(result.overall_status, "completed");
  assert.equal(result.scenarios.status, "completed");
  assert.equal(result.scan.max_cost_usd, 5);
  assert.equal(result.scan.model, "gpt-6-sol");
  assert.equal(result.scan.cyber_access_program, "daybreak_blue");
  assert.equal(scanOptions.cyberAccessProgram, "daybreak_blue");
  assert.equal(result.scan.plugin_version, "test-plugin");
  assert.equal(result.input_sha256[targetPaths[0]].length, 64);
  assert.deepEqual(scanOptions.target, targetPaths);
  assert.match(scanOptions.postScanPrompt, /different from any bundled lab bot/);
  assert.ok(scanOptions.postScanPrompt.includes(path.basename(outputRoot)));
  assert.ok((await fs.readFile(result.metadata_path, "utf8")).includes('"completed"'));
});

test("missing scenario output is reported as incomplete", async (t) => {
  const outputRoot = await testOutput(t);
  const client = mockClient(async () => ({
    manifest: { scan: { status: "completed" } },
    coverage: { deferred: ["unreviewed"] },
  }));
  const result = await executeReview(client, {
    repoRoot,
    options: parseOptions(["--max-cost-usd", "5", "--output-root", outputRoot]),
    now: fixedTime,
    repositoryState: fixedRepositoryState,
  });
  assert.equal(result.scan.status, "completed");
  assert.equal(result.scan.deferred_count, 1);
  assert.equal(result.scenarios.status, "incomplete");
  assert.equal(result.overall_status, "incomplete");
  assert.equal(result.failures[0].phase, "scenarios");
});

test("malformed scenario JSON leaves a completed scan with incomplete scenarios", async (t) => {
  const outputRoot = await testOutput(t);
  const client = mockClient(async (_repository, options) => {
    await fs.writeFile(path.join(path.dirname(options.outputDir), "scenarios.json"), "{broken");
    return { manifest: { scan: { status: "completed" } }, coverage: { deferred: [] } };
  });
  const result = await executeReview(client, {
    repoRoot,
    options: parseOptions(["--max-cost-usd", "5", "--output-root", outputRoot]),
    now: fixedTime,
    repositoryState: fixedRepositoryState,
  });
  assert.equal(result.scan.status, "completed");
  assert.equal(result.scenarios.status, "incomplete");
  assert.equal(result.failures[0].type, "SyntaxError");
});

test("scan interruption retains a manifest and failure details", async (t) => {
  const outputRoot = await testOutput(t);
  const client = mockClient(async () => {
    const error = new Error("Injected interruption");
    error.name = "ScanInterruptedError";
    error.scanDir = path.join(outputRoot, "partial-sdk");
    throw error;
  });
  const result = await executeReview(client, {
    repoRoot,
    options: parseOptions(["--max-cost-usd", "5", "--output-root", outputRoot]),
    now: fixedTime,
    repositoryState: fixedRepositoryState,
  });
  assert.equal(result.scan.status, "failed");
  assert.equal(result.scan.partial_output_dir, path.join(outputRoot, "partial-sdk"));
  assert.equal(result.overall_status, "incomplete");
  assert.equal(result.failures[0].type, "ScanInterruptedError");
  assert.ok(await fs.stat(result.metadata_path));
});

test("scenario validation checks family coverage, evidence paths, and CVE basis", async () => {
  const families = catalogFamilies({
    schema_version: "1.0",
    scenarios: [
      { family: "beta", id: "beta.1" },
      { family: "alpha", id: "alpha.2" },
      { family: "alpha", id: "alpha.1" },
    ],
  });
  assert.deepEqual([...families], [
    ["alpha", ["alpha.1", "alpha.2"]],
    ["beta", ["beta.1"]],
  ]);
  const targetPaths = ["testbeds/custom/datasets/v2/topology/network.yaml"];
  const report = scenarioOutput(families, targetPaths);
  assert.equal(validateScenarioReport(report, families, targetPaths), true);
  report.scenarios.pop();
  assert.throws(() => validateScenarioReport(report, families, targetPaths), /every catalog family/);
  const badEvidence = scenarioOutput(families, targetPaths);
  badEvidence.scenarios[0].steps[0].evidence_paths = ["testbeds/metropolis/devices/secret.py"];
  assert.throws(() => validateScenarioReport(badEvidence, families, targetPaths), /unselected evidence/);
  const badCve = scenarioOutput(families, targetPaths);
  badCve.scenarios[0].cves = [{ id: "CVE-2026-1234", product: "Device" }];
  assert.throws(() => validateScenarioReport(badCve, families, targetPaths), /version_evidence/);
});

test("the output root cannot be inside the repository", () => {
  assert.throws(() => resolveOutputRoot(repoRoot, path.join(repoRoot, "reports")), /outside/);
});

test("discovery follows a renamed testbed, new dataset, and added routers", async (t) => {
  const root = await testOutput(t);
  async function add(relativePath, contents = "x\n") {
    const absolute = path.join(root, relativePath);
    await fs.mkdir(path.dirname(absolute), { recursive: true });
    await fs.writeFile(absolute, contents);
  }
  await add("testbeds/other_plant/datasets/v2/topology/address-plan.yml");
  await add("testbeds/other_plant/datasets/v2/device_instances/new_devices.yaml");
  await add("testbeds/other_plant/datasets/v2/protocol_profiles/rates.yaml");
  await add("testbeds/other_plant/datasets/v2/topology/validation-report.md");
  await add("testbeds/other_plant/router/site/added_router.conf");
  await add("testbeds/other_plant/router/site/router_start.sh");
  await add("scripts/attacks/scenarios/v3.json", "{}\n");
  const selection = await discoverReviewTargets(root);
  assert.equal(selection.dataset, "testbeds/other_plant/datasets/v2");
  assert.equal(selection.catalog, "scripts/attacks/scenarios/v3.json");
  assert.deepEqual(selection.targetPaths, [
    "scripts/attacks/scenarios/v3.json",
    "testbeds/other_plant/datasets/v2/device_instances/new_devices.yaml",
    "testbeds/other_plant/datasets/v2/protocol_profiles/rates.yaml",
    "testbeds/other_plant/datasets/v2/topology/address-plan.yml",
    "testbeds/other_plant/router/site/added_router.conf",
    "testbeds/other_plant/router/site/router_start.sh",
  ]);
});

test("multiple datasets and catalogs need explicit selections", async (t) => {
  const root = await testOutput(t);
  async function add(relativePath) {
    const absolute = path.join(root, relativePath);
    await fs.mkdir(path.dirname(absolute), { recursive: true });
    await fs.writeFile(absolute, "{}\n");
  }
  await add("testbeds/alpha/datasets/v1/topology/network.yaml");
  await add("testbeds/alpha/datasets/v2/topology/network.yaml");
  await add("scripts/attacks/scenarios/v1.json");
  await add("scripts/attacks/scenarios/v2.json");
  await assert.rejects(discoverReviewTargets(root), /Select a dataset/);
  await assert.rejects(
    discoverReviewTargets(root, { dataset: "testbeds/alpha/datasets/v2" }),
    /Select a catalog/,
  );
  const chosen = await discoverReviewTargets(root, {
    dataset: "testbeds/alpha/datasets/v2",
    catalog: "scripts/attacks/scenarios/v2.json",
  });
  assert.ok(chosen.targetPaths.includes("testbeds/alpha/datasets/v2/topology/network.yaml"));
  assert.ok(!chosen.targetPaths.includes("testbeds/alpha/datasets/v1/topology/network.yaml"));
  await assert.rejects(
    discoverReviewTargets(root, {
      dataset: "../outside",
      catalog: "scripts/attacks/scenarios/v2.json",
    }),
    /Unknown dataset/,
  );
});

test("review execution uses a selected future dataset and its catalog", async (t) => {
  const root = await testOutput(t);
  const outputRoot = await testOutput(t);
  async function add(relativePath, contents) {
    const absolute = path.join(root, relativePath);
    await fs.mkdir(path.dirname(absolute), { recursive: true });
    await fs.writeFile(absolute, contents);
  }
  const dataset = "testbeds/another_testbed/datasets/v2";
  const catalog = "scripts/attacks/scenarios/new_catalog.json";
  await add(`${dataset}/topology/networks.yaml`, "networks: []\n");
  await add("testbeds/another_testbed/router/new_router.sh", "# router\n");
  await add(catalog, JSON.stringify({
    schema_version: "1.0",
    scenarios: [{ id: "case-1", family: "example" }],
  }));
  for (const prompt of ["scan-context.md", "scan-prompt.md", "scenario-prompt.md"]) {
    await add(
      `scripts/security/${prompt}`,
      await fs.readFile(path.join(repoRoot, "scripts/security", prompt), "utf8"),
    );
  }
  const targetPaths = [
    catalog,
    `${dataset}/topology/networks.yaml`,
    "testbeds/another_testbed/router/new_router.sh",
  ];
  let seenOptions;
  const client = mockClient(async (_repository, options) => {
    seenOptions = options;
    const reviewDir = path.dirname(options.outputDir);
    const families = new Map([["example", ["case-1"]]]);
    await fs.writeFile(
      path.join(reviewDir, "scenarios.json"),
      JSON.stringify(scenarioOutput(families, targetPaths)),
    );
    await fs.writeFile(path.join(reviewDir, "scenarios.md"), scenarioMarkdown(families));
    return { manifest: { scan: { status: "completed" } }, coverage: { deferred: [] } };
  });
  const result = await executeReview(client, {
    repoRoot: root,
    options: parseOptions([
      "--max-cost-usd", "1", "--output-root", outputRoot,
      "--dataset", dataset, "--catalog", catalog,
    ]),
    now: fixedTime,
    repositoryState: fixedRepositoryState,
  });
  assert.equal(result.overall_status, "completed");
  assert.deepEqual(seenOptions.target, targetPaths);
  assert.equal(result.scan.dataset, dataset);
  assert.equal(result.scan.catalog, catalog);
  assert.match(seenOptions.scanPrompt, /another_testbed\/datasets\/v2/);
  assert.match(seenOptions.postScanPrompt, /new_router\.sh/);
});
