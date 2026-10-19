# AWS configuration examples

These files illustrate the output format of `../provision-aws.ps1` and
`../verify_live.py`. Account ID `123456789012`, bucket names, and resource ARNs
are placeholders. `verification.json` shows the report format; it does not
verify your deployment.

Run the provisioning script with your own AWS profile and project resources.
It writes actual policies and resource identifiers to `../aws/`, which Git
ignores. Keep that generated directory and your `.env` local.
