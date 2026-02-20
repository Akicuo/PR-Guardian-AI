# PR Guardian AI - Deployment Guide

This guide will help you deploy PR Guardian AI with the new web application features.

## Prerequisites

- GitHub account
- Railway account (free tier works)
- OpenAI API key
- PostgreSQL database (Railway provides this)

## Step 1: Create GitHub App

1. Go to https://github.com/settings/apps
2. Click "New GitHub App"
3. Configure:
   - **App name**: PR Guardian AI
   - **Homepage URL**: `https://your-app.railway.app` (update after deploying)
   - **Callback URL**: `https://your-app.railway.app/auth/callback`
   - **Webhook URL**: `https://your-app.railway.app/webhook`
   - **Webhook secret**: Generate and save (use a random string)
4. Permissions:
   - Pull requests: **Read & Write**
   - Contents: **Read**
   - Metadata: **Read**
   - Checks: **Read** (optional)
5. Events:
   - Pull requests
   - Installation
   - Installation repositories
6. Click "Create GitHub App"
7. Generate a private key and download the `.pem` file
8. Note down:
   - GitHub App ID
   - Client ID
   - Client Secret
   - Webhook Secret
   - Private Key (content of .pem file)

## Step 2: Deploy to Railway

### 2a. Create Project and Deploy Code

1. Go to https://railway.app/
2. Click "New Project" → "Deploy from GitHub repo"
3. Select your `PR-Guardian-AI` repository
4. Click "Deploy Now"
5. Wait for deployment to complete

### 2b. Add PostgreSQL Database

1. In your Railway project, click "New Service"
2. Select "Database" → "Add PostgreSQL"
3. Railway will provision a PostgreSQL database
4. Click on the database service → "Variables"
5. Copy the `DATABASE_URL` value

### 2c. Configure Environment Variables

1. Go to your main app service (not the database)
2. Click "Variables" tab
3. Add the following variables:

```bash
# Required
OPENAI_API_KEY=sk-your-openai-key-here
DATABASE_URL=(paste from database service)
SECRET_KEY=(generate secure key, use: openssl rand -hex 32)
APP_URL=https://your-app.railway.app

# GitHub App
GITHUB_APP_ID=your-app-id
GITHUB_APP_CLIENT_ID=your-client-id
GITHUB_APP_CLIENT_SECRET=your-client-secret
GITHUB_APP_WEBHOOK_SECRET=your-webhook-secret
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
(paste entire PEM content)
...
-----END RSA PRIVATE KEY-----"

# Optional
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_ID=gpt-4o-mini
BOT_NAME=PR Guardian AI
LOG_LEVEL=info
```

4. Click "Save Changes" - Railway will restart your app

### 2d. Initialize Database

1. Click on your main app service
2. Click "Console" tab
3. Run the following command:

```bash
alembic upgrade head
```

4. You should see output like:
```
INFO  [alembic.runtime] Running upgrade...
```

## Step 3: Test the Application

### 3a. Test OAuth Flow

1. Open your app URL: `https://your-app.railway.app`
2. Click "Sign in with GitHub"
3. Authorize the GitHub App
4. You should be redirected to the dashboard

### 3b. Add Repository

1. Click "Repositories" in the navigation
2. Click "Sync with GitHub"
3. Find a repository you want to monitor
4. Click "Start Monitoring"
5. Select branches to monitor (default: main, master, develop)

### 3c. Test Webhook

1. Go to the monitored repository on GitHub
2. Create a new pull request
3. Within 10-20 seconds, the bot should post a code review comment
4. The comment will appear in the PR conversation

## Step 4: Verify Database Records

1. In Railway, go to your database service
2. Click "Query" to open the database viewer
3. Run these queries:

```sql
-- Check users
SELECT * FROM users;

-- Check monitored repositories
SELECT * FROM repositories WHERE is_monitored = true;

-- Check review history
SELECT * FROM review_history ORDER BY created_at DESC LIMIT 5;
```

## Troubleshooting

### OAuth Fails

**Problem**: Redirect loop or "Not authenticated"
- Check `GITHUB_APP_CLIENT_ID` and `GITHUB_APP_CLIENT_SECRET` are correct
- Check callback URL matches your app URL exactly
- Check `APP_URL` in environment variables

### Database Connection Fails

**Problem**: "Database connection error"
- Verify `DATABASE_URL` is correct
- Check database service is running
- Ensure database migrations were run with `alembic upgrade head`

### Webhook Not Triggering

**Problem**: Bot doesn't comment on PRs
- Check GitHub App webhook URL is correct
- Verify webhook secret matches
- Check webhook is being delivered (GitHub repo → Settings → Webhooks → Recent Deliveries)
- Check repository is monitored in database

### Review Not Saving to Database

**Problem**: Comments posted but not saved to database
- Check Railway logs for database errors
- Verify database connection is working
- Check `DATABASE_URL` has correct permissions

### AI Review Empty

**Problem**: Bot posts empty or error message
- Check `OPENAI_API_KEY` is valid
- Verify `OPENAI_BASE_URL` and `OPENAI_MODEL_ID` are correct
- Check API quota/credits
- Enable `LOG_LEVEL=debug` to see detailed logs

## Production Checklist

Before going to production:

- [ ] Generate a secure `SECRET_KEY` (use `openssl rand -hex 32`)
- [ ] Use HTTPS (Railway provides this automatically)
- [ ] Set `LOG_LEVEL=info` or `warning` in production
- [ ] Test GitHub App with your real repositories
- [ ] Monitor database storage limits (Railway free tier: 1GB)
- [ ] Set up error monitoring (Railway logs)
- [ ] Configure backup strategy for database
- [ ] Test webhook delivery with GitHub App settings
- [ ] Verify all environment variables are set

## Monitoring

### Railway Dashboard

- **Deployments**: View deployment history and logs
- **Metrics**: CPU, memory, and network usage
- **Logs**: Real-time application logs
- **Alerts**: Configure notifications for failures

### Key Metrics to Watch

- Database size (review history can grow quickly)
- API rate limits (GitHub: 5000 requests/hour)
- OpenAI API costs
- Response time for AI reviews

## Scaling Considerations

### When to Scale

- More than 100 monitored repositories
- More than 1000 PR reviews per day
- High concurrent webhook traffic

### Scaling Options

1. **Upgrade Railway plan**: More CPU/memory
2. **Add Redis caching**: For session management and rate limiting
3. **Use connection pooling**: Configure database connection pool size
4. **Background jobs**: Use Celery/Redis for async tasks
5. **Database optimization**: Add indexes, partition review_history table

## Next Steps

After successful deployment:

1. Set up custom domain (optional)
2. Configure email notifications
3. Add usage analytics
4. Implement rate limiting
5. Add more AI models/providers
6. Create user documentation
7. Set up monitoring and alerting

## Support

For issues or questions:
- Check Railway logs
- Review GitHub App settings
- Verify environment variables
- Check this guide's troubleshooting section
