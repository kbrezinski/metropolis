import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { promises as fs } from "node:fs";
import path from "node:path";

const REVIEW_PROMPTS = Object.freeze([
  "scripts/security/scan-context.md",
  "scripts/security/scan-prompt.md",
  "scripts/security/scenario-prompt.md",
]);
const DATASET_CONFIG_FOLDERS = ["topology", "device_instances", "protocol_profiles"];
const ROUTER_EXTENSIONS = new Set([".sh", ".conf", ".cfg", ".yaml", ".yml"]);

const ALLOWED_PATH_STATUSES = new Set([
  "permitted",
  "blocked",
  "same_vlan",
  "unknown",
]);
const ALLOWED_BASES = new Set(["configured", "inferred", "hypothetical"]);

export function parseOptions(args) {
  const options = {
    auth: "chatgpt",
    model: "gpt-6-sol",
    maxCostUsd: undefined,
    outputRoot: undefined,
    dataset: undefined,
    catalog: undefined,
    preflight: false,
    help: false,
  };

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--help" || arg === "-h") {
      options.help = true;
      continue;
    }
    if (arg === "--preflight") {
      options.preflight = true;
      continue;
    }
    if (!["--auth", "--model", "--max-cost-usd", "--output-root", "--dataset", "--catalog"].includes(arg)) {
      throw new Error(`Unknown option: ${arg}`);
    }
    const value = args[++index];
    if (!value || value.startsWith("--")) {
      throw new Error(`Missing value for ${arg}`);
    }
    if (arg === "--auth") {
      if (!["chatgpt", "api-key"].includes(value)) {
        throw new Error("--auth must be chatgpt or api-key");
      }
      options.auth = value;
    } else if (arg === "--model") {
      options.model = value;
    } else if (arg === "--output-root") {
      options.outputRoot = value;
    } else if (arg === "--dataset" || arg === "--catalog") {
      options[arg.slice(2)] = value;
    } else {
      const amount = Number(value);
      if (!Number.isFinite(amount) || amount <= 0) {
        throw new Error("--max-cost-usd must be a positive number");
      }
      options.maxCostUsd = amount;
    }
  }

  if (!options.help && !options.preflight && options.maxCostUsd === undefined) {
    throw new Error("A scan requires --max-cost-usd AMOUNT");
  }
  return options;
}

function relativeFile(root, file) {
  return path.relative(root, file).split(path.sep).join("/");
}

async function subdirectories(directory) {
  try {
    return (await fs.readdir(directory, { withFileTypes: true }))
      .filter((entry) => entry.isDirectory())
      .map((entry) => entry.name)
      .sort();
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
}

async function filesUnder(root, directory, extensions) {
  const files = [];
  async function visit(current) {
    let entries;
    try {
      entries = await fs.readdir(current, { withFileTypes: true });
    } catch (error) {
      if (error.code === "ENOENT") return;
      throw error;
    }
    for (const entry of entries) {
      const absolute = path.join(current, entry.name);
      if (entry.isDirectory()) await visit(absolute);
      else if (entry.isFile() && extensions.has(path.extname(entry.name).toLowerCase())) {
        files.push(relativeFile(root, absolute));
      }
    }
  }
  await visit(directory);
  return files.sort();
}

function selectOne(candidates, requested, label) {
  const normalized = requested?.replaceAll("\\", "/").replace(/\/$/, "");
  if (normalized && !candidates.includes(normalized)) {
    throw new Error(`Unknown ${label}: ${requested}. Available: ${candidates.join(", ") || "none"}`);
  }
  if (normalized) return normalized;
  if (candidates.length === 1) return candidates[0];
  if (candidates.length === 0) throw new Error(`No ${label} found`);
  throw new Error(`Select a ${label} explicitly: ${candidates.join(", ")}`);
}

export async function discoverReviewTargets(repoRoot, options = {}) {
  const testbedsRoot = path.join(repoRoot, "testbeds");
  const datasets = [];
  for (const testbed of await subdirectories(testbedsRoot)) {
    const datasetsRoot = path.join(testbedsRoot, testbed, "datasets");
    for (const name of await subdirectories(datasetsRoot)) {
      const candidate = `testbeds/${testbed}/datasets/${name}`;
      const folders = await Promise.all(
        DATASET_CONFIG_FOLDERS.map((folder) => filesUnder(
          repoRoot, path.join(repoRoot, candidate, folder), new Set([".yaml", ".yml"]),
        )),
      );
      if (folders.some((files) => files.length)) datasets.push(candidate);
    }
  }
  const dataset = selectOne(datasets, options.dataset, "dataset (--dataset)");
  const testbedRoot = dataset.slice(0, dataset.indexOf("/datasets/"));
  const configPaths = (await Promise.all(DATASET_CONFIG_FOLDERS.map((folder) =>
    filesUnder(repoRoot, path.join(repoRoot, dataset, folder), new Set([".yaml", ".yml"])),
  ))).flat();
  const routerPaths = await filesUnder(
    repoRoot, path.join(repoRoot, testbedRoot, "router"), ROUTER_EXTENSIONS,
  );
  const catalogs = await filesUnder(
    repoRoot, path.join(repoRoot, "scripts", "attacks", "scenarios"), new Set([".json"]),
  );
  const catalog = selectOne(catalogs, options.catalog, "catalog (--catalog)");
  return {
    dataset,
    catalog,
    targetPaths: [...new Set([...configPaths, ...routerPaths, catalog])].sort(),
  };
}

export function catalogFamilies(catalog) {
  if (catalog?.schema_version !== "1.0" || !Array.isArray(catalog.scenarios)) {
    throw new Error("The scenario catalog is missing or has an unsupported schema");
  }
  const families = new Map();
  const seenIds = new Set();
  for (const scenario of catalog.scenarios) {
    if (
      typeof scenario?.family !== "string" ||
      !scenario.family ||
      typeof scenario.id !== "string" ||
      !scenario.id ||
      seenIds.has(scenario.id)
    ) {
      throw new Error("The scenario catalog has an invalid or duplicate entry");
    }
    seenIds.add(scenario.id);
    const ids = families.get(scenario.family) ?? [];
    ids.push(scenario.id);
    families.set(scenario.family, ids);
  }
  if (families.size === 0) {
    throw new Error("The scenario catalog contains no attack families");
  }
  return new Map(
    [...families.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([family, ids]) => [family, ids.sort()]),
  );
}

function requireString(value, label) {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${label} must be a nonempty string`);
  }
}

function requireStringArray(value, label) {
  if (!Array.isArray(value)) {
    throw new Error(`${label} must be an array`);
  }
  value.forEach((item, index) => requireString(item, `${label}[${index}]`));
}

export function validateScenarioReport(report, families, targetPaths) {
  if (report?.schema_version !== "1.0" || report.review_kind !== "theoretical") {
    throw new Error("Scenario report must use the theoretical v1 schema");
  }
  if (
    typeof report.generated_at_utc !== "string" ||
    !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(
      report.generated_at_utc,
    ) ||
    Number.isNaN(Date.parse(report.generated_at_utc))
  ) {
    throw new Error("Scenario report needs a valid UTC generation time");
  }
  if (!Array.isArray(report.scenarios) || report.scenarios.length !== families.size) {
    throw new Error("Scenario report must cover every catalog family once");
  }

  const seenFamilies = new Set();
  for (const scenario of report.scenarios) {
    const family = scenario?.family;
    if (!families.has(family) || seenFamilies.has(family)) {
      throw new Error(`Unexpected or duplicate scenario family: ${family}`);
    }
    seenFamilies.add(family);
    const expectedIds = families.get(family);
    if (
      !Array.isArray(scenario.catalog_ids) ||
      scenario.catalog_ids.length !== expectedIds.length ||
      !expectedIds.every((id) => scenario.catalog_ids.includes(id))
    ) {
      throw new Error(`${family} does not cite all of its catalog IDs`);
    }
    for (const field of [
      "title",
      "initial_foothold",
      "command_infrastructure",
      "potential_ot_impact",
    ]) {
      requireString(scenario[field], `${family}.${field}`);
    }
    for (const field of [
      "prerequisites",
      "observables",
      "mitigations",
      "limitations",
    ]) {
      requireStringArray(scenario[field], `${family}.${field}`);
    }
    if (!Array.isArray(scenario.steps) || scenario.steps.length === 0) {
      throw new Error(`${family} needs at least one path step`);
    }
    for (const [index, step] of scenario.steps.entries()) {
      for (const field of ["source", "destination", "protocol", "description"]) {
        requireString(step?.[field], `${family}.steps[${index}].${field}`);
      }
      if (!ALLOWED_PATH_STATUSES.has(step.path_status)) {
        throw new Error(`${family}.steps[${index}] has an invalid path status`);
      }
      if (!ALLOWED_BASES.has(step.basis)) {
        throw new Error(`${family}.steps[${index}] has an invalid evidence basis`);
      }
      requireStringArray(step.evidence_paths, `${family}.steps[${index}].evidence_paths`);
      for (const evidencePath of step.evidence_paths) {
        if (!targetPaths.includes(evidencePath)) {
          throw new Error(`${family} cites an unselected evidence path: ${evidencePath}`);
        }
      }
    }
    if (!Array.isArray(scenario.cves)) {
      throw new Error(`${family}.cves must be an array`);
    }
    for (const cve of scenario.cves) {
      if (!/^CVE-\d{4}-\d{4,}$/i.test(cve?.id ?? "")) {
        throw new Error(`${family} has an invalid CVE identifier`);
      }
      requireString(cve.product, `${family}.cves.product`);
      requireString(cve.version_evidence, `${family}.cves.version_evidence`);
      if (typeof cve.source_url !== "string" || !cve.source_url.startsWith("https://")) {
        throw new Error(`${family} needs an HTTPS advisory source for each CVE`);
      }
    }
  }
  return true;
}

export function validateScenarioMarkdown(markdown, families) {
  if (typeof markdown !== "string" || !/^\|\s*Family\s*\|/im.test(markdown)) {
    throw new Error("Scenario Markdown needs its family summary table");
  }
  for (const family of families.keys()) {
    if (!markdown.toLowerCase().includes(family.toLowerCase())) {
      throw new Error(`Scenario Markdown omits ${family}`);
    }
  }
  if (!/theoretical/i.test(markdown) || !/GNS3/i.test(markdown)) {
    throw new Error("Scenario Markdown must state its theoretical GNS3 scope");
  }
  return true;
}

function gitOutput(repoRoot, args) {
  return execFileSync("git", args, {
    cwd: repoRoot,
    encoding: "utf8",
    windowsHide: true,
  }).trim();
}

function isInside(directory, candidate) {
  const relative = path.relative(directory, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

export function resolveOutputRoot(repoRoot, requested) {
  const result = path.resolve(
    requested ?? path.join(path.dirname(repoRoot), "metropolis-security-results"),
  );
  if (isInside(repoRoot, result)) {
    throw new Error("Security review output must be outside the repository");
  }
  return result;
}

async function hashInputs(repoRoot, targetPaths) {
  const hashes = {};
  for (const relativePath of [...targetPaths, ...REVIEW_PROMPTS]) {
    const bytes = await fs.readFile(path.join(repoRoot, relativePath));
    hashes[relativePath] = createHash("sha256").update(bytes).digest("hex");
  }
  return hashes;
}

async function writeJson(filePath, value) {
  const temporary = `${filePath}.tmp-${process.pid}`;
  await fs.writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await fs.rename(temporary, filePath);
}

function messageOf(error) {
  return String(error?.message ?? error).slice(0, 1000);
}

function resultLocation(filePath) {
  return filePath ?? null;
}

function makePostScanPrompt(template, families, jsonPath, markdownPath) {
  const familyLines = [...families.entries()]
    .map(([family, ids]) => `- ${family}: ${ids.join(", ")}`)
    .join("\n");
  return template
    .replace("{{FAMILIES}}", familyLines)
    .replace("{{JSON_PATH}}", jsonPath.replaceAll("\\", "/"))
    .replace("{{MARKDOWN_PATH}}", markdownPath.replaceAll("\\", "/"));
}

export async function executeReview(
  client,
  {
    repoRoot,
    options,
    now = () => new Date(),
    repositoryState = (root) => ({
      revision: gitOutput(root, ["rev-parse", "HEAD"]),
      dirty: gitOutput(root, ["status", "--porcelain"]) !== "",
    }),
  },
) {
  const root = path.resolve(repoRoot);
  const { dataset, catalog: catalogPath, targetPaths } = await discoverReviewTargets(root, options);
  const outputRoot = resolveOutputRoot(root, options.outputRoot);
  const { revision, dirty } = repositoryState(root);
  const startedAt = now().toISOString();
  const runId = `${startedAt.replace(/[-:.]/g, "")}_${revision.slice(0, 8)}`;
  const runDir = path.join(outputRoot, runId);
  const sdkDir = path.join(runDir, "sdk");
  const jsonPath = path.join(runDir, "scenarios.json");
  const markdownPath = path.join(runDir, "scenarios.md");
  const catalog = JSON.parse(
    await fs.readFile(path.join(root, catalogPath), "utf8"),
  );
  const families = catalogFamilies(catalog);
  const scanPrompt = await fs.readFile(
    path.join(root, "scripts/security/scan-prompt.md"),
    "utf8",
  );
  const scenarioPrompt = await fs.readFile(
    path.join(root, "scripts/security/scenario-prompt.md"),
    "utf8",
  );
  const knowledgeBasePaths = [path.join(root, "scripts/security/scan-context.md")];
  const preflightOptions = {
    target: targetPaths,
    mode: "standard",
    knowledgeBasePaths,
    outputDir: sdkDir,
    auth: options.auth,
    cyberAccessProgram: "daybreak_blue",
  };
  if (options.preflight) {
    const preflight = await client.preflight(root, preflightOptions);
    return {
      status: "preflight",
      repository: root,
      dataset,
      catalog: catalogPath,
      model: options.model,
      cyber_access_program: "daybreak_blue",
      target_paths: targetPaths,
      output_dir: sdkDir,
      plan: preflight,
    };
  }

  await fs.mkdir(outputRoot, { recursive: true, mode: 0o700 });
  await fs.mkdir(runDir, { recursive: false, mode: 0o700 });
  const metadataPath = path.join(runDir, "review-metadata.json");
  const metadata = {
    schema_version: "1.0",
    run_id: runId,
    started_at_utc: startedAt,
    completed_at_utc: null,
    repository: { path: root, git_revision: revision, dirty },
    input_sha256: await hashInputs(root, targetPaths),
    scan: {
      status: "preflight",
      dataset,
      catalog: catalogPath,
      target_paths: targetPaths,
      model: options.model,
      cyber_access_program: "daybreak_blue",
      auth: options.auth,
      max_cost_usd: options.maxCostUsd,
      sdk_output_dir: sdkDir,
      report_path: null,
      findings_path: null,
      coverage_path: null,
      manifest_path: null,
      plugin_version: null,
      estimated_cost_usd: null,
      deferred_count: null,
      warnings: [],
    },
    scenarios: {
      status: "pending",
      json_path: jsonPath,
      markdown_path: markdownPath,
      expected_families: [...families.keys()],
    },
    failures: [],
    overall_status: "running",
  };
  await writeJson(metadataPath, metadata);

  try {
    await client.preflight(root, preflightOptions);
  } catch (error) {
    metadata.scan.status = "failed";
    metadata.scenarios.status = "not_started";
    metadata.failures.push({
      phase: "preflight",
      reason: messageOf(error),
      type: error?.name ?? "Error",
    });
    metadata.completed_at_utc = now().toISOString();
    metadata.overall_status = "incomplete";
    await writeJson(metadataPath, metadata);
    return { ...metadata, metadata_path: metadataPath };
  }
  metadata.scan.status = "running";
  await writeJson(metadataPath, metadata);

  const postScanPrompt = makePostScanPrompt(
    scenarioPrompt,
    families,
    jsonPath,
    markdownPath,
  );
  let result;
  try {
    result = await client.run(root, {
      ...preflightOptions,
      scanPrompt: `Selected dataset: ${dataset}\nSelected attack catalog: ${catalogPath}\n\n${scanPrompt}`,
      postScanPrompt: `Selected dataset: ${dataset}\nSelected attack catalog: ${catalogPath}\nSelected evidence paths:\n${targetPaths.map((target) => `- ${target}`).join("\n")}\n\n${postScanPrompt}`,
      maxCostUsd: options.maxCostUsd,
      onWarning(warning) {
        const message = messageOf(warning);
        metadata.scan.warnings.push(message);
      },
    });
    metadata.scan.status = result?.manifest?.scan?.status ?? "unknown";
    metadata.scan.report_path = resultLocation(result?.reportPath);
    metadata.scan.findings_path = resultLocation(result?.findingsPath);
    metadata.scan.coverage_path = resultLocation(result?.coveragePath);
    metadata.scan.manifest_path = resultLocation(result?.manifestPath);
    metadata.scan.plugin_version = result?.pluginVersion ?? null;
    metadata.scan.estimated_cost_usd = result?.cost?.estimatedUsd ?? null;
    metadata.scan.deferred_count = Array.isArray(result?.coverage?.deferred)
      ? result.coverage.deferred.length
      : null;
    if (metadata.scan.status !== "completed") {
      metadata.failures.push({ phase: "scan", reason: `SDK status: ${metadata.scan.status}` });
    }
  } catch (error) {
    metadata.scan.status = "failed";
    metadata.scan.partial_output_dir = resultLocation(error?.scanDir);
    metadata.failures.push({ phase: "scan", reason: messageOf(error), type: error?.name ?? "Error" });
  }
  await writeJson(metadataPath, metadata);

  try {
    const report = JSON.parse(await fs.readFile(jsonPath, "utf8"));
    const markdown = await fs.readFile(markdownPath, "utf8");
    validateScenarioReport(report, families, targetPaths);
    validateScenarioMarkdown(markdown, families);
    metadata.scenarios.status = "completed";
    metadata.scenarios.generated_at_utc = report.generated_at_utc;
  } catch (error) {
    metadata.scenarios.status = "incomplete";
    metadata.failures.push({ phase: "scenarios", reason: messageOf(error), type: error?.name ?? "Error" });
  }
  metadata.completed_at_utc = now().toISOString();
  metadata.overall_status =
    metadata.scan.status === "completed" && metadata.scenarios.status === "completed"
      ? "completed"
      : "incomplete";
  await writeJson(metadataPath, metadata);
  return { ...metadata, metadata_path: metadataPath };
}
