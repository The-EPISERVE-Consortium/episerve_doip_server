# Creating a Workflow FDO

The Creator develops a workflow designed for maximum portability and registers the complete package as a persistent FDO artifact. This involves three main steps:

1. **Develop** a Snakemake pipeline with version-locked Conda environments for each rule
2. **Package** the workflow and its metadata as a Workflow RO-Crate archive
3. **Register** the archive in the MaRDI knowledge graph so it can be resolved and executed via its identifier

## 1. Develop the Snakemake Pipeline

The workflow must explicitly use the Snakemake **Conda integration** to ensure cross-platform reproducibility.

1. **Write the Snakefile:** Define the logic, rules, inputs, and outputs of your pipeline.

2. **Define Isolated Environments:** For every external tool or dependency, create a dedicated **`environment.yaml`** file listing version-locked dependencies (e.g., `fastqc=0.11.9`, `python=3.9`).

3. **Link Environments in the Snakefile:** Ensure every rule points to its environment file using the `conda:` directive.

    Example Snakefile snippet:

    ```python
    rule fastqc_report:
        input:
            "data/{sample}.fastq"
        output:
            "results/{sample}_fastqc.html"
        conda:
            "envs/fastqc.yaml"
        shell:
            "fastqc {input} -o {output}"
    ```

4. **Test for Portability:** Test the workflow on Windows, Linux, and macOS using the `--use-conda` flag to confirm environments build correctly and execution is identical across systems.

---

## 2. Package and Register the FDO

The validated workflow files are packaged as a **[Workflow RO-Crate](https://w3id.org/workflowhub/workflow-ro-crate/)** and registered in the MaRDI knowledge graph. The Workflow RO-Crate profile is an EOSC/ELIXIR standard for packaging executable workflows with enriched metadata, ensuring the package is interpretable by tools and communities beyond MaRDI without requiring registration in any external registry.

### 2.1 Assemble Workflow Artifacts

Collect the following into a single directory before crating:

- `Snakefile` — the main workflow definition
- `envs/` — all `environment.yaml` files
- Any configuration files
- `README.md` — description of the workflow, inputs, outputs, and parameters
- `CITATION.cff` — citation metadata for proper attribution
- `test/` — test input data and expected outputs (see section 3)

### 2.2 Generate the Workflow RO-Crate

From inside the workflow directory, use the `rocrate` CLI (included in `requirements.txt`):

```bash
rocrate init
rocrate add workflow -l snakemake Snakefile
rocrate add file README.md
rocrate add file CITATION.cff
rocrate write-zip workflow_A01.crate.zip
```

Richer metadata — such as creator ORCID, Snakemake version, or parameter descriptions — can be added by editing the generated `ro-crate-metadata.json` directly before packaging.

### 2.3 Register in the MaRDI Knowledge Graph

A workflow FDO has two parts: the **RO-Crate archive** is stored in MaRDI storage, while the **FDO metadata** — the item that makes the workflow findable and resolvable — lives in the **MaRDI knowledge graph**, which serves as a registry for datasets and research artifacts from the broader community.

Registration involves two steps:

1. **Create a knowledge graph item** for the workflow (if one does not already exist), recording its metadata (creator, description, parameters, Snakemake version). This gives the workflow a stable QID.
2. **Upload the RO-Crate** to MaRDI storage under the workflow's QID with component ID `rocrate`.

Once uploaded, the MaRDI FDO server automatically detects the file and lists it in the FDO manifest under `fdo:hasComponent`. The MaRDI DOIP server can then resolve and serve the workflow by its QID without any further steps.

---

Next: [Workflow Testing](workflows_testing.md) | [Executing a Workflow](workflows_execute.md)
