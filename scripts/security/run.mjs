#!/usr/bin/env node

import path from "node:path";
import { fileURLToPath } from "node:url";
import { executeReview, parseOptions } from "./review.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function printHelp() {
  process.stdout.write(`Metropolis on-demand topology security review

Usage:
  node scripts/security/run.mjs --preflight [--dataset PATH] [--catalog PATH] [--model ID] [--auth chatgpt|api-key] [--output-root PATH]
  node scripts/security/run.mjs --max-cost-usd AMOUNT [--dataset PATH] [--catalog PATH] [--model ID] [--auth chatgpt|api-key] [--output-root PATH]

The SDK scan and theoretical scenarios use declared repository settings only.
Daybreak Blue is selected for scans; the model defaults to gpt-6-sol.
Results are saved outside the Git worktree. No GNS3 or attack commands run.
`);
}

let client;
try {
  const options = parseOptions(process.argv.slice(2));
  if (options.help) {
    printHelp();
  } else {
    const { CodexSecurity } = await import("@openai/codex-security");
    client = new CodexSecurity({ codexOverrides: { model: options.model } });
    const result = await executeReview(client, { repoRoot, options });
    if (options.preflight) {
      process.stdout.write(
        `Preflight passed for ${result.target_paths.length} selected files.\n` +
          `Dataset: ${result.dataset}\nCatalog: ${result.catalog}\n` +
          `Model: ${result.model}\nCyber access: ${result.cyber_access_program}\n` +
          `Proposed SDK output: ${result.output_dir}\n` +
          "Preflight does not check authentication or execute a scan.\n",
      );
    } else {
      process.stdout.write(
        `Review status: ${result.overall_status}\n` +
          `Review metadata: ${result.metadata_path}\n` +
          `SDK report: ${result.scan.report_path ?? "unavailable"}\n` +
          `Theoretical scenarios: ${result.scenarios.status === "completed" ? result.scenarios.json_path : "unavailable"}\n`,
      );
      if (result.overall_status !== "completed") process.exitCode = 1;
    }
  }
} catch (error) {
  process.stderr.write(`${error?.name ?? "Error"}: ${error?.message ?? error}\n`);
  process.exitCode = 1;
} finally {
  await client?.close();
}
