# Executing a Workflow

The User executes a verified workflow on their local machine using its PID and local Conda installation. Complete [System Preparation](workflows_preparation.md) before proceeding.

## 1. Run the FDO Client

The FDO client automates the download, verification, and Snakemake execution steps.

1. **Initiate Run:** Execute the workflow using its PID and specify the local input directory.

    ```bash
    fdo-run-client 21.11152/workflow_A01 --input "C:\User\Data\RawSequences" --cores 4
    ```

2. **Client Verification Steps:**

    - **Resolve PID:** Query the MaRDI DOIP server to retrieve the storage URL and original checksum.
    - **Download & Extract:** Download the RO-Crate and extract it to a temporary working directory.
    - **Verify Integrity:** Calculate the checksum of the downloaded archive. Execution stops if it does not match the PID record.
    - **Prepare:** Map the user input directory into the Snakemake configuration.

3. **Snakemake Execution:** The client launches the Snakemake run command:

    ```bash
    snakemake -s /path/to/Snakefile --cores 4 --use-conda --conda-frontend mamba --config input_path="/path/to/input/data"
    ```

---

## 2. Reproducibility Across Platforms

Because the `--use-conda` flag is used:

- **Snakemake** reads the `environment.yaml` files from the downloaded RO-Crate.
- **Mamba** creates isolated environments with exact dependency versions compiled for the user's OS.
- The workflow runs identically across Windows, Linux, and macOS without requiring Docker or virtualization.
