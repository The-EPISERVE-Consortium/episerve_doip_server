# Workflow Testing

Include test cases inside the RO-Crate using the **[Workflow Testing RO-Crate](https://w3id.org/ro/wftest)** extension. This adds a formal declaration of test inputs and expected outputs, making it possible to verify the workflow still produces correct results after any environment or dependency change.

Add a `test/` directory to your workflow package:

```
test/
  input/           # minimal sample input data
  expected/        # expected output files
  test_config.yaml
```

Declare the test in `ro-crate-metadata.json` using the `TestSuite` and `TestInstance` types defined by the Workflow Testing RO-Crate profile.

---

Next: [Executing a Workflow](workflows_execute.md)
