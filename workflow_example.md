# Snakemake Workflow Example: Octave Eigenvalue Analysis

This reproducible workflow requires three main files to be included in your FDO (RO-Crate): the data input, the Conda environment definition, the Octave script, and the Snakemake pipeline itself.

## 1. The Input Data (data/matrix.csv)

```
1.0, 0.5, 0.2
0.5, 2.0, 0.3
0.2, 0.3, 3.0
```

## 2. Conda Environment (envs/octave_analysis.yaml)

```
channels:
  - conda-forge
dependencies:
  - octave=6.4.0
  - python=3.9
  - pandas=1.3
  - matplotlib=3.4
```

## 3. Octave Script (scripts/eigen_analysis.m)

```
% scripts/eigen_analysis.m

input_file = argv(){1};
output_file = argv(){2};

M = csvread(input_file);

[V, D] = eig(M);

eigenvalues = diag(D);

csvwrite(output_file, eigenvalues);

disp("Octave analysis complete. Eigenvalues saved.");
```

## 4. Snakemake Pipeline (Snakefile)

```
configfile: "config.yaml"

rule all:
    input:
        "results/eigenvalues_plot.png"

rule compute_eigenvalues:
    input:
        "data/matrix.csv"
    output:
        "results/eigenvalues.csv"
    params:
        octave_script = "scripts/eigen_analysis.m"
    conda:
        "envs/octave_analysis.yaml"
    shell:
        "octave --no-gui --silent {params.octave_script} {input} {output}"

rule plot_eigenvalues:
    input:
        "results/eigenvalues.csv"
    output:
        "results/eigenvalues_plot.png"
    params:
        plot_script = "scripts/plot_eigenvalues.py"
    conda:
        "envs/octave_analysis.yaml"
    script:
        '''
        import pandas as pd
        import matplotlib.pyplot as plt
        import numpy as np

        eigenvalues = pd.read_csv(
            snakemake.input[0], 
            header=None
        ).values

        eigenvalues = np.sort(eigenvalues)

        plt.figure(figsize=(8, 6))
        plt.bar(
            range(1, len(eigenvalues) + 1), 
            eigenvalues
        )

        plt.xlabel('Eigenvalue Index')
        plt.ylabel('Eigenvalue Magnitude')
        plt.title('Eigenvalues of the 3x3 Matrix')
        plt.xticks(range(1, 4))
        plt.grid(axis='y')

        plt.savefig(snakemake.output[0])
        print(snakemake.output[0])
        '''
```

## Summary of Execution (PID to Plot)

This summary details the entire automated process, from the user providing the **Persistent Identifier (PID)** to the final plot generation, ensuring the role of the DoIP/FDO system is clearly represented.

1.  **PID Resolution & Retrieval:**
    * The user initiates the run via the **FDO Client** using the workflow's PID.
    * The **FDO Client** queries the **DoIP server** to resolve the PID, retrieving the RO-Crate's storage URL and its original **Checksum**.
    * The Client downloads the RO-Crate archive (containing the `Snakefile`, `envs/`, `scripts/`, etc.) and extracts the contents to a temporary local directory.

2.  **Integrity Verification:**
    * The Client calculates a new checksum of the downloaded RO-Crate and compares it to the value retrieved from the DoIP server.
    * **Execution proceeds only if the checksums match**, guaranteeing the integrity and reproducibility of the workflow artifact.

3.  **Snakemake Orchestration:**
    * The Client calls the local `snakemake --use-conda` command within the temporary directory.

4.  **Rule 1: `compute_eigenvalues` (Octave):**
    * **Conda** creates the isolated environment containing the specified version of **Octave**.
    * The rule executes the Octave script (`scripts/eigen_analysis.m`).
    * Octave reads the input matrix and saves the computed eigenvalues to the intermediate file, `results/eigenvalues.csv`.

5.  **Rule 2: `plot_eigenvalues` (Python/Matplotlib):**
    * Snakemake uses the **same Conda environment** (which contains Python, Pandas, and Matplotlib).
    * The Python script reads the `results/eigenvalues.csv` file.
    * It generates the bar plot visualization and saves the final result as `results/eigenvalues_plot.png`. 

6.  **Cleanup and Output:**
    * The workflow finishes, and the **FDO Client** presents the final output file(s) to the user.
    * The Client performs cleanup, removing the temporary directory and the created Conda environments.