# Reproducible Workflows with Snakemake & FDO (Conda-Based)

This section describes how to use Snakemake workflows as Fair Digital Objects (FDOs) managed by the MaRDI DOIP server. The process is split into two main roles:

- **[System Preparation](workflows_preparation.md)** — one-time setup of Mamba/Conda, Snakemake, and the FDO client on Windows, Linux, or macOS
- **[Creating a Workflow FDO](workflows_create.md)** — developing, packaging, and registering a workflow as an FDO (Creator role)
- **[Executing a Workflow](workflows_execute.md)** — fetching and running a registered workflow via its PID (User role)

See also: [workflow example](workflow_example.md).

## How It Works

The MaRDI knowledge graph already models the relationships between research artifacts — publications, methods, datasets, and workflows — as connected items. Making a workflow FAIR within this infrastructure means ensuring that the actual files are stored and linked to those existing items, so the MaRDI DOIP server can resolve and serve them.

The process for a Creator comes down to three steps:

1. **Create a workflow item** in the MaRDI knowledge graph to obtain a stable QID
2. **Package** the workflow and its dependencies as a [Workflow RO-Crate](workflows_create.md) — a self-contained, executable archive
3. **Upload** the RO-Crate to MaRDI storage under the workflow's QID

Once uploaded, the MaRDI FDO server automatically detects the stored file and exposes it in the FDO manifest. The MaRDI DOIP server can then resolve and serve the workflow by its QID without any further registration steps.

Anyone can then retrieve the workflow, its metadata, and the associated datasets through the MaRDI DOIP server using standard identifiers — and re-execute it reproducibly on any platform.

## Considerations

### Conda

This strategy relies on **Conda** environment management to ensure reproducibility across platforms without needing Docker or OS compatibility layers. **Conda** is the underlying package manager and environment system; several distributions bundle it. **Miniforge** is the recommended choice: it defaults to the community-maintained **conda-forge** channel, ships with **Mamba** (a significantly faster dependency resolver) out of the box, and carries no licensing restrictions. The alternatives have drawbacks — Miniconda defaults to the `defaults` channel (Anaconda ToS), and Anaconda ships ~3 GB of pre-installed packages with commercial licensing restrictions for larger organisations.

| Distribution | Size | Default Channel | Includes Mamba | License |
|---|---|---|---|---|
| **Miniforge** ✓ | Minimal | conda-forge | Yes | No restrictions |
| Miniconda | Minimal | defaults | No | Anaconda ToS |
| Anaconda | ~3 GB | defaults | No | Commercial restrictions |

### EOSC Compatibility

Workflows are kept within the **MaRDI ecosystem** but packaged using **EOSC-compatible standards**, meaning they can be understood and reused by tools and communities in the broader EOSC/ELIXIR landscape without requiring registration in any external registry. Compatibility is achieved through packaging standards rather than external registration — Snakemake remains the sole execution engine, and workflows stay registered exclusively within the MaRDI DoIP infrastructure. A workflow packaged this way can be imported into WorkflowHub or consumed by any EOSC-compatible tooling at any point in the future, without changes to the workflow itself.

| Standard | Role | Status |
|---|---|---|
| Workflow RO-Crate | Packaging format | ✓ Adopted |
| Workflow Testing RO-Crate | Test case declaration | ✓ Adopted |
| `ro-crate-py` | RO-Crate generation tooling | ✓ Adopted |
| `CITATION.cff` | Attribution metadata | ✓ Adopted |
| Snakemake version pinning | Reproducibility | ✓ Adopted |
| CWL | Engine-agnostic workflow description | — Not required |
| WorkflowHub registration | External registry | — Not required |

### Reproducibility

Reproducibility is guaranteed at three levels. At the **environment level**, every Snakemake rule declares a version-locked `environment.yaml`; Mamba rebuilds identical isolated environments from these on any platform. At the **engine level**, the Snakemake version itself is pinned inside the RO-Crate metadata, so the workflow always runs against the same engine it was developed with. At the **data level**, the DoIP server stores a SHA-256 checksum alongside each registered RO-Crate; the FDO client verifies this checksum before execution and aborts if it does not match, ensuring the workflow has not been altered since registration.

### Licensing

The choice of Conda distribution has licensing implications for organisations: Anaconda and Miniconda use the `defaults` channel, which is subject to Anaconda's commercial terms for organisations above a certain size. Miniforge avoids this entirely by defaulting to the community-maintained `conda-forge` channel. Workflow artifacts themselves should include a `CITATION.cff` file to declare authorship and terms of reuse, and a `README.md` to document intended use — both are required components of a valid Workflow RO-Crate package.
