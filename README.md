# PR Guardian AI

### **AI-Powered Pull Request Reviewer for GitHub**

<p align="center">
  <img src="https://github.com/user-attachments/assets/cd861495-a460-43df-bfd7-4fdf3aabfdc2" width="250" alt="PR Guardian AI Logo">
</p>


<p align="center">
<!-- Badges -->
<a href="#"><img src="https://img.shields.io/badge/Status-Active-success?style=for-the-badge"></a> <a href="#"><img src="https://img.shields.io/badge/AI%20Powered-OpenAI-blue?style=for-the-badge&logo=openai"></a> <a href="#"><img src="https://img.shields.io/badge/FastAPI-Backend-009688?style=for-the-badge&logo=fastapi"></a> <a href="#"><img src="https://img.shields.io/badge/GitHub-App-black?style=for-the-badge&logo=github"></a> <a href="#"><img src="https://img.shields.io/badge/License-MIT-yellow?style=for-the-badge"></a>
</p>

---
 
## Overview

**PR Guardian AI** is an advanced GitHub App that automatically reviews pull requests using artificial intelligence.
It reads your code diffs, finds problems, and writes professional comments inside the PR, just like a human reviewer.

This tool helps developers deliver high-quality code faster, reduces review workload, and provides consistent feedback across teams.

---

## Why PR Guardian AI?
 
### Features

| Feature                             | Description                                                        |
| ----------------------------------- | ------------------------------------------------------------------ |
|    AI-powered Code Review           | Automatically analyzes PR diffs using OpenAI.                      |
|    Detects Code Issues              | Finds bugs, security risks, optimization issues, unused code, etc. |
|    Comments Inside PR               | Posts human-like comments directly in the conversation.            |
|    Real-Time Webhook Processing     | Handles PR events instantly (opened, updated).                     |
|    Verification Loop               | Validates AI claims against actual code to prevent false positives. |
|    Configurable Verification        | Control verification depth via environment variables.              |
|    Secure GitHub App Authentication | Uses JWT & installation token best practices.                      |
|    Works on Any Repository          | Easy installation & setup.                                         |
|    Developer Friendly               | Fully open-source & customizable.                                  |

---

## How It Works (Full Explanation)
 
### **1. A Pull Request is created or updated**

GitHub triggers a webhook event:
`pull_request` with action `opened`, `synchronize`, etc.

### **2. GitHub sends PR data to your backend**
 
Your backend receives it on:

```
POST /webhook
```

It contains:

* PR number
* repo information
* diff URL
* installation ID

### **3. The backend validates the signature**

Using `X-Hub-Signature-256` and your `GITHUB_WEBHOOK_SECRET`.

### **4. Backend authenticates as GitHub App**

It generates:

* JWT
* Installation Access Token

### **5. Backend fetches the PR diff**

Using:

```
https://patch-diff.githubusercontent.com/raw/.../pull/<id>.diff
```

### **6. AI analyzes the code diff**

It sends diff to OpenAI with a structured prompt:

* detect bugs
* find performance issues
* detect bad naming
* security warnings
* suggest improvements

### **7. Verification loop validates claims**

The bot posts an initial "analyzing" comment, then verifies its claims:
* Reads actual file content from the PR branch using GitHub API
* Validates claims like "file is incomplete" or "missing import"
* Corrects false claims before posting the final review
* Configurable via `MAX_VERIFICATION_CALLS` (default: 20, use `-1` for unlimited)

### **8. App posts verified comments in the PR**

Using:

```
POST /repos/{owner}/{repo}/issues/{pr_number}/comments
```

---

## Architectural Diagram

```
 ┌──────────────┐
 │ Developer    │
 │ creates PR   │
 └──────┬───────┘
        │
        ▼
 ┌────────────────────┐
 │ GitHub Webhook     │────────────┐
 └──────┬─────────────┘            │
        │ (pull_request event)     │
        ▼                          │
 ┌───────────────────────┐         │
 │ FastAPI Backend       │         │
 │ /webhook              │         │
 └──────┬────────────────┘         │
        │                          │
        ▼                          │
 ┌─────────────────────────┐       │
 │ Verify Signature        │       │
 └──────┬──────────────────┘       │
        │                          │
        ▼                          │
 ┌─────────────────────────────┐   │
 │ Generate GitHub JWT         │   │
 │ Get Installation Token      │   │
 └──────┬──────────────────────┘   │
        │                          │
        ▼                          │
 ┌──────────────────────────────┐  │
 │ Post "Analyzing" Comment     │  │
 └──────┬───────────────────────┘  │
        │                          │
        ▼                          │
 ┌──────────────────────────────┐  │
 │ Fetch PR diff (.diff)        │  │
 └──────┬───────────────────────┘  │
        │                          │
        ▼                          │
 ┌────────────────────────────┐    │
 │ Send code to OpenAI        │    │
 │ Generate Draft Review      │    │
 └──────┬─────────────────────┘    │
        │                          │
        ▼                          │
 ┌─────────────────────────────┐   │
 │ Verification Loop           │   │
 │ - Extract claims            │   │
 │ - Fetch actual files        │   │
 │ - Validate claims           │   │
 │ - Refine review             │   │
 └──────┬──────────────────────┘   │
        │                          │
        ▼                          │
 ┌─────────────────────────────┐   │
 │ GitHub API: Update Comment  │◄──┘
 │ with Verified Review        │
 └─────────────────────────────┘
```

---

## Installation (Developer Mode)

### 1. Clone repo

```bash
git clone https://github.com/AmirhosseinHonardoust/PR-Guardian-AI.git
cd github-ai-reviewer
```

### 2. Install requirements

```bash
pip install -r requirements.txt
```

### 3. Create `.env`

```
GITHUB_TOKEN=your-github-pat
GITHUB_WEBHOOK_SECRET=your-webhook-secret
OPENAI_API_KEY=your-openai-key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_ID=gpt-4o-mini
BOT_NAME=PR Guardian AI
LOG_LEVEL=info
MAX_VERIFICATION_CALLS=20
```

**Environment Variables:**
- `GITHUB_TOKEN` - GitHub Personal Access Token with repo access
- `GITHUB_WEBHOOK_SECRET` - Optional webhook secret for signature verification
- `OPENAI_API_KEY` - Your OpenAI API key (or compatible endpoint)
- `OPENAI_BASE_URL` - OpenAI API base URL (default: `https://api.openai.com/v1`)
- `OPENAI_MODEL_ID` - Model to use for reviews (default: `gpt-4o-mini`)
- `BOT_NAME` - Name displayed in comments (default: `PR Guardian AI`)
- `LOG_LEVEL` - Logging level (default: `info`)
- `MAX_VERIFICATION_CALLS` - Max GitHub API calls for verification (default: `20`, use `-1` for unlimited)

### 4. Run server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## Webhook Proxy (Local Development)

### Using Smee.io

```bash
npm install --global smee-client

smee --url https://smee.io/YOUR_ID --target http://localhost:8000/webhook
```

GitHub → App settings → Webhook URL

```
https://smee.io/YOUR_ID
```

---

## Deployment Options

| Platform                 | Status                | Difficulty |
| ------------------------ | --------------------- | ---------- |
| **Railway**              |   Recommended         |  Easy      |
| **Render**               |   Works well          |  Medium    |
| **DigitalOcean Droplet** |                       |  Medium    |
| **Heroku**               |   Requires paid Dyno  |            |
| **VPS / Bare-metal**     |   Full control        |            |

---

## Testing

Create or update a pull request →
Check PR conversation →
AI comments should appear automatically.

If not:

* Check GitHub delivery logs
* Check backend logs
* Check Smee console

---

## Contributing

Pull requests are welcome.

You can contribute:

* Better AI prompts
* Support for multiple file types
* Line-by-line review
* Security scanning
* Performance analysis

---

## License

Distributed under the **MIT License**.

---

## Credits

Built with care by **Amir Hossein Honardoust**
Helping developers write clean, optimized, and secure code using AI.

If you like this project
**Star the repo**
and
Share with others!
