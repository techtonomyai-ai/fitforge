# FitForge — AI Personal Trainer App

AI-powered personalized workouts, nutrition tracking, and meal plans.
Built with FastAPI + OpenAI GPT-5.4 + Stripe.

## Revenue Model
- Basic: $9.99/month (workouts + nutrition tracking)
- Pro: $14.99/month (+ AI meal plans + weekly AI adjustments)
- 1,000 users × $12 avg = $12,000/month
- 10,000 users × $12 avg = $120,000/month

## API Keys Needed
1. **OpenAI** → Already have it! (sk-proj-...)
2. **Stripe** → stripe.com → create 2 recurring products ($9.99 + $14.99)

## Quick Start
```bash
cd fitforge
cp .env.example .env
# Fill in API keys

pip install -r requirements.txt

cd backend
uvicorn main:app --reload --port 8000
```

Open frontend/index.html in browser.

## Deploy to Production
```bash
# Railway.app (recommended — $5/month)
railway login
railway init
railway up
```

## Features
- AI workout plan generator (GPT-5.4 powered)
- 50+ exercise library with instructions
- Daily nutrition tracker (calories, protein, carbs, fat)
- AI weekly meal plans
- Progress tracking (weight, body fat)
- Weekly AI plan adjustments
- Stripe subscriptions + 7-day free trial

## Content Strategy (B2C)
- Film morning workouts → TikTok/YouTube Shorts
- Show FitForge generating your plan in real time
- "Link in bio" → free trial → $9.99/month
- Iris (@IrisGoddessofall) cross-promotes daily

Built by Techtonomy LLC — techtonomy.ai
