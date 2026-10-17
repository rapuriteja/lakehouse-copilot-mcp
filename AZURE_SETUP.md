# Deploy Lakehouse Copilot to Azure: a beginner's guide

This guide sets up the project on **your own Azure subscription**. It uses:

- **ADLS Gen2** for the lakehouse files;
- **Azure Databricks** with Unity Catalog for the pipeline;
- **Key Vault** for the keys;
- **Azure AI Search** (free tier) for catalog search;
- **OpenRouter** for the LLM and the embeddings.

Do every step from a terminal on your laptop. Allow about 1 to 2 hours the first time.

> **Money.** Some of this costs money while it runs, mainly Databricks compute and the SQL
> warehouse. Read [Costs](#11-costs) first, and run [step 12](#12-delete-everything) when you are
> done. Nothing here is created until you run `terraform apply`.

---

## 0. What you will end up with

```
your laptop                         Azure (one resource group)
-----------                         --------------------------------------------
lhc ask / lhc ui  ───────────────▶  Databricks SQL warehouse ──▶ Unity Catalog gold tables
        │                                       ▲
        │                           Databricks job: generate → bronze → silver → gold
        │                                       → publish_docs → index_search
        │                                                              │
        ├──── catalog search ─────▶  Azure AI Search (free) ◀─────────┘ (embeddings via OpenRouter)
        ├──── keys (az login) ────▶  Key Vault: openrouter-api-key, azure-search-api-key
        └──── LLM calls ──────────▶  OpenRouter (outside Azure)
```

---

## 1. Before you start

**Accounts**

- An Azure subscription where you are **Owner**. Contributor alone is not enough, because
  Terraform also creates role assignments.
- An OpenRouter account with a little credit, and an API key from
  <https://openrouter.ai/settings/keys>. Treat the key like a password: never paste it into a
  file that git tracks, a chat or a screenshot.

**Tools on your laptop**

| Tool | Check it works | Install |
|---|---|---|
| Git | `git --version` | <https://git-scm.com> |
| Python 3.10+ | `python --version` | <https://www.python.org> |
| Java 17+ (for local Spark) | `java -version` | Temurin from <https://adoptium.net> |
| Azure CLI | `az version` | <https://learn.microsoft.com/cli/azure/install-azure-cli> |
| Terraform 1.6+ | `terraform version` | <https://developer.hashicorp.com/terraform/install> |
| Databricks CLI 0.230+ | `databricks version` | <https://docs.databricks.com/dev-tools/cli/install> |

**Run it locally first.** If the local version works, cloud problems are easier to find.

```bash
git clone https://github.com/rapuriteja/lakehouse-copilot-mcp.git
cd lakehouse-copilot-mcp
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev,databricks]"
lhc generate && lhc pipeline && lhc index
lhc ask "What was the net revenue in 2025?"
```

---

## 2. Log in to Azure

```bash
az login                                         # opens the browser
az account list -o table                         # find your subscription
az account set --subscription "<subscription name or id>"
az account show -o table                         # check it is the right one
```

Register the services this project uses. You only do this once per subscription, and it is free:

```bash
for ns in Microsoft.Databricks Microsoft.Search Microsoft.KeyVault Microsoft.Storage \
          Microsoft.OperationalInsights Microsoft.Insights; do
  az provider register --namespace $ns
done
```

---

## 3. Create the infrastructure with Terraform

**Terraform** reads the `.tf` files and creates the Azure resources they describe.
`plan` shows what will happen and `apply` does it.

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars    # names and region; nothing secret in it
```

Open `terraform.tfvars` and check the values. `location = "centralindia"` keeps the data in
India, and `search_sku = "free"` uses the free AI Search tier. Only one free search service is
allowed per subscription. If you already have one, set `search_sku = "basic"`, which is paid.

Give Terraform your OpenRouter key **through an environment variable**, so it never lands in a
file. Terraform writes it into Key Vault as the secret `openrouter-api-key`:

```bash
read -s TF_VAR_openrouter_api_key && export TF_VAR_openrouter_api_key   # paste, press Enter
```

`read -s` hides what you type, and the key does not go into your shell history.

```bash
terraform init                    # downloads the Azure providers
terraform plan -out tfplan        # read the list: about 20 resources "to add", nothing to destroy
terraform apply tfplan            # takes 5-10 minutes
terraform output                  # the names and URLs you need next
```

Keep this terminal open. The next steps use `terraform output`.

> **State file.** Terraform remembers what it created in `terraform.tfstate`. That file also
> contains the keys it wrote to Key Vault. It is git-ignored, so keep it on your laptop only.
> For a team, store it in a private storage account instead (`backend.tf.example`).

📸 **Screenshot 1:** the resource group in the Azure portal, showing all the resources.

---

## 4. Log in to Databricks

```bash
export DATABRICKS_HOST=$(terraform output -raw databricks_workspace_url)
databricks auth login --host "$DATABRICKS_HOST"     # opens the browser
databricks current-user me                           # should print your email
```

---

## 5. Set up Unity Catalog (one time)

**Unity Catalog** is Databricks' governance layer: catalogs, schemas, tables, permissions and
tags. New workspaces are enabled for it automatically. The pipeline needs a catalog called
`lakehouse_copilot` that stores its tables in your ADLS `lakehouse` container.

In the Databricks workspace, open **Catalog** (left menu), then the gear icon, then
**External data**:

1. **Credentials → Create credential.** Pick type *Azure Managed Identity*, name it
   `lakehouse-copilot-cred`, and paste the access connector id from
   `terraform output -raw databricks_access_connector_id`.
2. **External locations → Create external location.** Name it `lakehouse-copilot-lake`, set the
   URL to `terraform output -raw lakehouse_storage_url` and the credential to
   `lakehouse-copilot-cred`. Click **Test connection**; every check should pass.
3. Create the catalog from your terminal:

```bash
databricks catalogs create lakehouse_copilot \
  --storage-root "$(terraform output -raw lakehouse_storage_url)"
```

The bundle creates the schemas (`landing`, `bronze`, `silver`, `gold`) and the volume itself.

---

## 6. Connect Databricks to Key Vault (secret scope)

A **secret scope** is a named place in Databricks that holds secrets. A *Key Vault-backed* scope
reads straight from your Key Vault. The job cluster then gets the keys as environment variables
(`{{secrets/lakehouse-copilot/openrouter-api-key}}` in `resources/medallion_job.yml`).
Databricks hides them in logs. Terraform has already given Databricks read access to the
vault (`kv_databricks` role).

1. Open `https://<your workspace URL>#secrets/createScope`. Copy the workspace URL from
   `terraform output -raw databricks_workspace_url` and add `#secrets/createScope` to the end.
   The page is not in the menu.
2. Fill in:
   - **Scope Name:** `lakehouse-copilot`
   - **Manage Principal:** *All workspace users* (or *Creator*)
   - **DNS Name:** `terraform output -raw key_vault_uri`
   - **Resource ID:** `terraform output -raw key_vault_id`
3. Click **Create**, then check it:

```bash
databricks secrets list-secrets lakehouse-copilot
# expect: azure-search-api-key and openrouter-api-key (names only, never values)
```

---

## 7. Deploy and run the pipeline (Databricks Asset Bundle)

A **Databricks Asset Bundle** describes the job, schemas and volume in YAML next to the code.
`deploy` builds the Python wheel and creates everything in the workspace.

```bash
cd ../..                                            # back to the repository root
SEARCH=$(terraform -chdir=infra/terraform output -raw azure_search_endpoint)
databricks bundle validate -t dev --var="search_endpoint=$SEARCH"
databricks bundle deploy   -t dev --var="search_endpoint=$SEARCH"
databricks bundle run      -t dev medallion_pipeline --var="search_endpoint=$SEARCH"
```

`bundle run` waits and prints a link to the run. The tasks run in order, on small single-node
clusters that start for the job and stop after it:

`generate_data → bronze → silver → gold → publish_docs → index_search`

📸 **Screenshot 2:** the job run graph with all six tasks green (Workflows, then the job, then the run).

📸 **Screenshot 3:** Catalog Explorer showing `lakehouse_copilot.gold.dim_customer` with its
column comments and the `pii` tag on `email`, `phone` and `full_name`.

📸 **Screenshot 4:** the AI Search index `lakehouse-catalog` in the Azure portal, showing the
document count (52).

---

## 8. Point the copilot at Azure and test it

The copilot on your laptop needs a **SQL warehouse** (Databricks compute that runs SQL) to query
the gold tables.

1. In Databricks, open **SQL Warehouses → Create**. Choose a *Serverless* warehouse, size
   *2X-Small*, and set **Auto stop to 10 minutes**. Open it, then **Connection details**, and
   copy the *HTTP path*.
2. Create a personal access token: click your profile, then **Settings → Developer → Access
   tokens**, and give it a short lifetime.
3. Write `.env` in the repository root. It is git-ignored, but check with
   `git check-ignore .env`:

```bash
terraform -chdir=infra/terraform output -raw env_file_hint > .env
cat >> .env <<'EOF'
DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/<id>
LLM_MODEL=google/gemini-2.5-flash   # the model chosen in evals/README.md
EOF
echo "DATABRICKS_TOKEN=<paste the token>" >> .env   # or edit .env in your editor
```

You do not put the OpenRouter or Search keys in `.env`. `KEY_VAULT_URI` is in it, so the app
reads them from Key Vault with your `az login`.

4. Test:

```bash
lhc sql "SELECT COUNT(*) FROM gold.fact_sales"                # runs on Databricks
lhc search "festive season"                                   # Azure AI Search
lhc ask "Top 5 cities by revenue during the festive season" -v
lhc ui                                                        # http://localhost:8501
```

📸 **Screenshot 5:** `lhc ask ... -v` in the terminal, showing the SQL, the sources and the
OpenRouter cost line.

📸 **Screenshot 6:** the Streamlit UI answering a question, with "How I answered" open.

📸 **Screenshot 7:** the OpenRouter activity page (<https://openrouter.ai/activity>), showing
the requests and their cost. Crop out anything that shows your key.

Optional: run the golden-set evaluation against Azure with
`lhc eval --model <model> --out evals/results/azure`.

---

## 9. Optional: deploy from GitHub Actions

`deploy.yml` does steps 3 and 7 from GitHub, with no passwords stored. It logs in with
**OIDC**: GitHub proves who it is to Azure with a short-lived token.

1. In Entra ID, create an app registration (for example `gh-lakehouse-copilot`) and add a
   **federated credential**. Choose the *GitHub Actions* scenario, set organization
   `rapuriteja`, repository `lakehouse-copilot-mcp`, entity *Environment*, name `dev`.
2. Give that app **Contributor** and **Role Based Access Control Administrator** on the
   subscription, or on a resource group made for this project.
3. Create a storage account for the Terraform state, with a container `tfstate`.
4. In GitHub, open **Settings → Environments** and create `dev`. Add the secrets
   `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `TFSTATE_RESOURCE_GROUP`,
   `TFSTATE_STORAGE_ACCOUNT`, `OPENROUTER_API_KEY` and `DATABRICKS_CLIENT_ID`. The last one is a
   Databricks service principal with a GitHub federation policy; see the Databricks docs on
   "OAuth token federation".
5. Add the variable `AZURE_DATABRICKS_SP_OBJECT_ID`. It is the object id of the AzureDatabricks
   app in your tenant, and you get it with:
   `az ad sp show --id 2ff814a6-3304-4ab8-85cb-cd0e6f879c1d --query id -o tsv`
6. Open **Actions → Deploy to Azure → Run workflow**. Run it first with *apply* unticked, which
   only plans, and read the plan.

---

## 10. Screenshots for your portfolio

| # | What | Where |
|---|---|---|
| 1 | Resource group with all resources | Azure portal |
| 2 | Job run graph, all tasks green | Databricks Workflows |
| 3 | `dim_customer` comments and `pii` tags | Databricks Catalog Explorer |
| 4 | Search index with 52 documents | Azure portal, AI Search |
| 5 | `lhc ask -v` with SQL and cost | Terminal |
| 6 | Chat UI answering a question | Browser |
| 7 | OpenRouter requests and cost | openrouter.ai/activity |

Put them in `docs/images/` and link them from the README. Before you commit, check that no key,
token or subscription id is visible in them.

---

## 11. Costs

Prices change, so check the [Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/)
and your OpenRouter activity page. What costs money here:

| Item | When you pay | How to keep it low |
|---|---|---|
| Databricks job clusters (VM + DBUs) | only while a job runs | single node, runs on demand (the schedule is paused) |
| Databricks SQL warehouse | only while it is running | 2X-Small, auto-stop 10 minutes |
| OpenRouter | per request | cheap model; one `lhc eval` run is 40 questions |
| Storage, Key Vault, Log Analytics | small, per use | 30-day log retention, little data |
| Azure AI Search | **free tier** costs nothing | keep `search_sku = "free"` |

New Azure accounts often come with starting credit, and Databricks offers a trial. Check the
current offers when you sign up.

After the first run, open **Cost Management → Cost analysis** for the resource group. That way
you know your real cost per run and don't have to guess.

---

## 12. Delete everything

1. Drop the Unity Catalog objects. They live in the account-level metastore, so deleting the
   workspace does not remove them:

```bash
databricks catalogs delete lakehouse_copilot --force
databricks external-locations delete lakehouse-copilot-lake
databricks storage-credentials delete lakehouse-copilot-cred
```

2. Delete the SQL warehouse and your access token in Databricks.
3. Destroy the Azure resources:

```bash
cd infra/terraform
terraform destroy        # type "yes"; takes 5-10 minutes
az group list -o table   # the rg-lhcopilot-dev group should be gone
```

Key Vault is purged on destroy (`purge_soft_delete_on_destroy`), so you can reuse the names.

4. Optionally, revoke the OpenRouter key at <https://openrouter.ai/settings/keys>.

---

## 13. If something goes wrong

| Error | What it means | Fix |
|---|---|---|
| `AuthorizationFailed` on a role assignment | You are not Owner | Ask for Owner, or *Role Based Access Control Administrator* |
| `Operation could not be completed as it results in exceeding approved ... Cores quota` | No VM quota in the region | Request quota for the *Ddsv5* family, or change `node_type` in `databricks.yml` |
| Search: `only one free service per subscription` | You already have a free search service | Set `search_sku = "basic"` (paid) or delete the other one |
| Secret scope page: "access denied" to Key Vault | The Databricks role assignment is missing | Re-run `terraform apply`; check the `kv_databricks` role on the vault |
| `index_search` fails with `AZURE_SEARCH_ENDPOINT is not set` | The bundle variable is missing | Pass `--var="search_endpoint=..."` as in step 7 |
| `lhc ask` says it runs offline | No OpenRouter key was found | Check `KEY_VAULT_URI` in `.env` and that `az login` is done |
