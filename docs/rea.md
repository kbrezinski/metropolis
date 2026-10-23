# Using REA with Metropolis

[REA (Reverse Engineer Anything)](https://github.com/morluto/rea) is an optional,
independent tool for investigating software. Metropolis does not bundle REA,
analysis engines, or target binaries. Read REA's [original README](https://github.com/morluto/rea#readme),
[installation guide](https://github.com/morluto/rea/blob/main/docs/installation.md),
and [disclaimer](https://github.com/morluto/rea/blob/main/DISCLAIMER.md) for its
current requirements and supported workflows.

## Connect REA

Clone the [official REA repository](https://github.com/morluto/rea) into
`third_party/rea/` under the Metropolis checkout. `third_party/` is Git
ignored. The local `examples/rea-main` snapshot, if present, is only a
reference. Follow REA's current [README](https://github.com/morluto/rea#readme)
and [installation guide](https://github.com/morluto/rea/blob/main/docs/installation.md)
for its prerequisites, source build, Codex setup, approval, restart, and
readiness checks. Install and register REA on the host where Codex will launch
its local MCP server.

REA setup registers its MCP server and workflow instructions with the selected
agent. REA may need a separate analysis engine for the target type; use its
current provider and readiness guidance rather than relying on commands copied
into this guide.

## Start an investigation

For advanced, authorized REA-assisted work in Codex, Daybreak Red is recommended
when separately approved and available. Daybreak Blue or another suitable
option is an alternative. Check OpenAI's current
[Daybreak guidance](https://learn.chatgpt.com/docs/cyber-safety) for access and
appropriate use. This choice is made in Codex, not REA, and does not change the
separate Codex Security review tool's access requirements.

Provide these inputs to the agent:

- **Required:** the absolute path to a locally obtained binary or other
  supported artifact, and your freeform investigation question.
- **Optional:** a source URL for provenance, a preferred REA analysis provider
  when more than one is available, and a location for the results.

The source URL is contextual information, not proof of permission. This guide
does not download target software or prescribe a default investigation prompt.
Metropolis does not provide instructions for reverse engineering or modifying
malware. Follow REA's own documentation for its supported analysis workflows.

## Register a new attack artifact

If an authorized investigation produces a new executable for a Metropolis
attack scenario, place it under the appropriate `scripts/attacks/<family>/`
folder. Follow the [attack toolkit's contribution guidance](../scripts/attacks/README.md#adding-an-attack-artifact)
to give it provenance and register its scenario in the versioned catalog. The
Codex Security review uses that catalog to recognize attack families; it does
not inspect or execute the executable.

## Authorization and licenses

You are responsible for obtaining the target software and authorization for
your analysis and tools. If you modify malware, you are responsible for keeping
that work within authorized research or blue-team exercises in an appropriate
isolated environment. Do not deploy modified malware on systems you are not
authorized to test. Permission to inspect a file does not necessarily authorize
executing it or interacting with another party's systems.

The following commercial-tool notice is from [REA's original README](https://github.com/morluto/rea#license):

> You must legally obtain and be authorized to use commercial tools, including
> IDA Pro / Hex-Rays decompilers, Hopper, and JEB, under their applicable
> licenses. REA grants no tool license, key, or activation entitlement. Trials
> and demos remain subject to vendor terms, including user, feature,
> automation, and output sharing restrictions. Cracked copies, stolen keys,
> unauthorized key sharing, and bypassing license restrictions are not
> authorized. This guidance does not amend MIT or third-party licenses.
