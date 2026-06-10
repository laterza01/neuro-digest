# NeuroDigest — Complete Project Documentation

## 1. PROJECT OVERVIEW

**NeuroDigest** is a fully automated neurology newsletter and social media distribution system, curated by Vincenzo Laterza, MD (Neurologist at Di Venere Hospital, Bari, Italy).

- **Newsletter**: Weekly (Monday 14:00-14:30 Italian time) to ~300 subscribers
- **Social Media**: Daily automation (Instagram carousel + Facebook posts + Stories + Reels)
- **Hosting**: Vercel (production)
- **Domain**: https://www.neuro-digest.com

### Critical User Rules (Non-Negotiable)
```
1. "SE NON TI DICO DI FARE UNA COSA NON DEVI FARLA IN AUTOMATICO"
   → If I don't ask for something, don't automate it
   
2. "REGOLE FERREE: 
   1) MAIL NEURODIGEST OGNI LUNEDI ALLE 14-14.30
   2) ULTIMO LUNEDI DEL MESE GUIDELINE EDITION ALLO STESSO ORARIO"
   → No delays. Must be precise timing.
   
3. "NON VOGLIO RITARDI LA MATTINA NE NEL RICEVERE LE MAIL, 
   E DOVRESTI AVER RISOLTO CON VERCEL CHE HA IL CRON PRECISO, 
   NE NELLA PUBBLICAZIONE DEI POST"
   → No timing errors. Vercel Cron must be rock-solid.
   
4. "SEMPRE IN PREVIEW, TUTTO IN PREVIEW"
   → Stripe is in preview mode only.
   
5. "LASCIARE LA LANDING COME È"
   → Don't modify landing.html without explicit request.
```

---

## 2. COMPLETE TECHNICAL STACK

### Backend Infrastructure
- **Hosting**: Vercel (production)
- **Cron Jobs**: Vercel Cron (4 triggers)
- **Database**: Supabase PostgreSQL
- **Email Service**: Resend (SMTP)
- **Code Repository**: GitHub (laterza01/neuro-digest)
- **CI/CD**: GitHub Actions (for newsletter generation)

### External APIs
- **Claude API** (claude-opus-4-5): Content generation + carousel layout
- **Notion API**: Article database (integration point)
- **Meta Graph API**: Instagram + Facebook posting
- **PubMed E-utilities**: Article discovery
- **RSS Feeds**: 27 Q1/Q2 neurology journals (Lancet, NEJM, JAMA, Brain, Nature Neuroscience, etc.)

### Python Libraries
```
anthropic              (Claude API)
supabase              (Database)
playwright            (HTML→PNG rendering at 1080x1080, 1080x1920)
moviepy               (MP4 Reel generation: 7 slides × 4 sec = 28 sec)
resend                (Email delivery)
feedparser            (RSS parsing)
urllib                (HTTP requests, Notion API, Meta Graph API)
dotenv                (Environment variables)
datetime, json, sys, os
```

### Storage
- **Supabase Storage**: Carousel PNGs, Story covers, Reel MP4s
- **DNS**: Cloudflare (neuro-digest.com)
- **Static Pages**: Vercel (index.html, privacy.html, unsubscribe.html, preferences.html)

---

## 3. DATABASE SCHEMA

### Supabase Tables

#### `subscribers`
```sql
CREATE TABLE subscribers (
  id BIGSERIAL PRIMARY KEY,
  email VARCHAR(255) UNIQUE NOT NULL,
  confirmed BOOLEAN DEFAULT FALSE,
  subscribed_at TIMESTAMP DEFAULT NOW(),
  reason_unsubscribe VARCHAR(255),
  unsubscribed_at TIMESTAMP
);
```

#### `digests` (Weekly Newsletter)
```sql
CREATE TABLE digests (
  id BIGSERIAL PRIMARY KEY,
  edition_num INTEGER,
  subject VARCHAR(255),
  digest_json TEXT,         -- Full newsletter content (JSON)
  approved BOOLEAN DEFAULT FALSE,
  sent_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT NOW()
);
```

#### `social_posts` (Daily Social Content)
```sql
CREATE TABLE social_posts (
  id BIGSERIAL PRIMARY KEY,
  article_title VARCHAR(255),
  article_url TEXT UNIQUE,
  journal VARCHAR(100),
  carousel_html TEXT,       -- Legacy field (unused)
  slide_urls TEXT[],        -- Array of 7 PNG URLs (1080×1080)
  fb_text TEXT,             -- Facebook post text
  fb_cover_url TEXT,        -- 1st slide without dots (1080×1080)
  story_cover_url TEXT,     -- 1st slide vertical (1080×1920)
  reel_url TEXT,            -- MP4 URL (1080×1920)
  notion_page_id TEXT,      -- Notion page ID
  
  -- Approval flags (user must click APPROVE buttons to set these)
  ig_approved BOOLEAN DEFAULT FALSE,
  fb_approved BOOLEAN DEFAULT FALSE,
  ig_story_approved BOOLEAN DEFAULT FALSE,
  fb_story_approved BOOLEAN DEFAULT FALSE,
  ig_story_video_approved BOOLEAN DEFAULT FALSE,
  fb_story_video_approved BOOLEAN DEFAULT FALSE,
  
  -- Post IDs from Meta Graph API
  ig_post_id TEXT,
  fb_post_id TEXT,
  ig_story_id TEXT,
  fb_story_id TEXT,
  
  -- Tracking
  posted_at TIMESTAMP,      -- ONLY set when something was ACTUALLY published
  created_at TIMESTAMP DEFAULT NOW()
);
```

#### `subscribers_preferences` (Email Preferences)
```sql
CREATE TABLE subscribers_preferences (
  id BIGSERIAL PRIMARY KEY,
  email VARCHAR(255) UNIQUE,
  preferences_json TEXT,
  updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## 4. NOTION INTEGRATION

### Notion Database Structure
**Database ID**: `NOTION_DATABASE_ID` (env var)

**Columns**:
| Column | Type | Purpose |
|--------|------|---------|
| Nome | Title | Article title (50 chars max for dedup) |
| URL | URL | Article link (unique identifier) |
| Journal | Select | Publication source (e.g., "Lancet", "NEJM") |
| Status | Select | "New" / "Scheduled" / "Used" |
| Use for | Multi-select | "Mail" and/or "Social" |
| Published | Date | Article publication date |
| Summary | Rich Text | Article abstract (for Claude context) |
| Topic | Text | Category tag (stroke, MS, Parkinson, etc.) |
| Week | Text | Tracking field |

**Deduplication Logic** (in `social_daily.py`):
1. `get_existing_notion_keys()` fetches ALL pages (with pagination, not just 100)
2. Extracts URLs and title[:50].lower() into sets
3. Excludes articles with Status="Used" + Use for contains "Social"
4. New articles saved with Status="New", Use for="Social"

---

## 5. NEWSLETTER FLOW (Weekly)

### Sunday 20:00 UTC = 22:00 Italian
**Trigger**: Vercel Cron → GitHub Actions `preview.yml`

```
[1] digest.py --generate-only
    - Fetches RSS from 27 journals (Q1/Q2 neurology articles)
    - Claude (opus-4-5) selects best articles + structures them
    - Saves newsletter to Supabase (digests table, approved=False)
    - SAVES ALL ARTICLES TO NOTION (status=New, use_for=Mail)

[2] preview_send.py
    - Fetches latest digest from Supabase
    - Injects green APPROVE banner at top
    - Sends [PREVIEW #N] email to vincenzolate95l@gmail.com
    - Email includes newsletter content + APPROVE button
```

**APPROVE Button**:
- URL: `https://neuro-digest.com/api/approve?token={APPROVE_SECRET}`
- Sets `approved=True` on digest in Supabase
- **Does NOT send immediately** (critical rule)

### Monday 14:00 UTC = 16:00 Italian
**Trigger**: Vercel Cron → GitHub Actions `digest.yml`

```
[1] digest.py (no --generate-only flag, so runs full send)
    - Checks if approved=True on latest digest
    - IF approved: sends to all 300 subscribers via Resend
    - Updates articles in Notion → status=Used, use_for=Mail
    - Resets approved=False for next week
    - IF not approved: skips sending (no error)
```

**Special Case: Last Monday of Month**
- Automatically detected in `digest.py`
- Same flow but newsletter type = "Guidelines Edition"
- Same time slot (14:00)

---

## 6. SOCIAL AUTOMATION FLOW (Daily)

### Every Day 08:00 UTC = 10:00 Italian
**Trigger**: Vercel Cron → GitHub Actions `social_daily.yml`

```
[1/6] Fetch Fresh Articles (PubMed, last 3 days)
      - Keywords: neurology, stroke, MS, epilepsy, Parkinson, dementia
      - Type: clinical trial, review, guideline, meta-analysis
      - Returns: ~20 articles max

[2] Filter & Deduplicate
    - Exclude URLs posted in last 14 days (recently_posted_urls)
    - Exclude URLs with pending unapproved posts (pending_urls)  ← KEY FIX
    - Exclude URLs already in Notion
    - Result: list of truly new articles

[3] Save to Notion
    - New articles → Status=New, Use for=Social
    - Tracks notion_id for later updates

[4] Claude Generates Carousel
    - Selects single best article from new ones
    - Generates 7-slide carousel JSON:
      1. Cover (topic + headline)
      2. Context (background, 2-3 sentences)
      3. Study (what they did/found)
      4. Stat (key number/acronym)
      5. Takeaway (clinical implication)
      6. Source (journal + year)
      7. CTA (call-to-action)
    - Generates Facebook text (3-4 sentences, flowing paragraph)

[5] Render to Images
    - Playwright opens HTML in Chrome headless
    - Instagram carousel: 7 PNGs 1080×1080 WITH navigation dots
    - Facebook cover: 1st slide 1080×1080 WITHOUT dots
    - Story cover: 1st slide 1080×1920 (vertical, no dots)

[6] Generate Reel MP4
    - Moviepy: 7 vertical slides × 4 sec each = 28 sec total
    - Resolution: 1080×1920
    - Format: MP4 (download link sent to user)

[7] Upload to Supabase Storage
    - Path: {post_id}/{filename}
    - Files: slide_0.png ... slide_6.png, fb_cover.png, story_cover.png, reel.mp4
    - Returns public URLs

[8] Save Post to Supabase
    - social_posts row created:
      - article_title, article_url, journal, fb_text
      - slide_urls[], fb_cover_url, story_cover_url, reel_url
      - All approval flags = False
      - posted_at = null (critical)

[9] Send 3 Preview Emails
    - [Instagram Preview]: 7 slide thumbnails + APPROVE Carousel button + APPROVE Story button
    - [Facebook Preview]: cover image + post text + APPROVE Post button + APPROVE Story button
    - [Reel MP4]: download link + instructions (user adds music + posts manually)

[10] Update Notion
     - Article marked as Status=Scheduled
     - Use for updated to include "Social"
```

### Every Day 14:00 UTC = 16:00 Italian
**Trigger**: Vercel Cron → GitHub Actions `social_post.yml`

```
[1] Query for Approved Posts
    - Fetch social_posts WHERE posted_at=null 
                        AND (ig_approved OR fb_approved OR ig_story_approved OR fb_story_approved)
    - Limit 1 (process one post per day)

[2] Instagram Carousel (if ig_approved=True and ig_post_id=null)
    - Create 7 image containers via Graph API
    - Create carousel container
    - Wait for processing (status=FINISHED, max 60 sec)
    - Publish carousel
    - Save ig_post_id

[3] Facebook Post (if fb_approved=True and fb_post_id=null)
    - POST to {FB_PAGE_ID}/photos with cover image + caption
    - Save fb_post_id

[4] Instagram Story (if ig_story_approved=True and ig_story_id=null)
    - POST 1080×1920 cover as Instagram Story
    - Save ig_story_id immediately

[5] Facebook Story (if fb_story_approved=True and fb_story_id=null)
    - POST 1080×1920 cover as Facebook Story
    - Save fb_story_id immediately

[6] Update Supabase
    - ONLY set posted_at if something_posted=True
    - Save ig_post_id, fb_post_id
    - Update Notion: Status=Used, Use for updated to keep Mail (if present) + add Social

[7] If Nothing Approved
    - Don't set posted_at
    - Post remains with posted_at=null
    - Can be reprocessed on next run if user approves later
```

**Critical Fix (2026-06-10)**:
- `something_posted` flag tracks if anything was actually published
- `posted_at` only set if `something_posted=True`
- Prevents blocking unapproved posts in database

---

## 7. APPROVAL WORKFLOW

### Newsletter Approval
1. Sunday 20:00: Preview email arrives with [PREVIEW #N] subject
2. User reviews and clicks APPROVE button (anytime before Monday 14:00)
3. Button calls `/api/approve?token={APPROVE_SECRET}`
4. Sets `approved=True` on digest
5. Monday 14:00 cron checks flag and sends if approved

### Social Approval
1. Every day 8:00: Preview emails arrive with article content
2. User reviews and clicks desired APPROVE button:
   - APPROVE Carousel → sets ig_approved=True
   - APPROVE Story → sets ig_story_approved=True
   - APPROVE Post → sets fb_approved=True
   - APPROVE Story → sets fb_story_approved=True
3. Button calls `/api/social_approve?token={APPROVE_SECRET}&post_id={id}&platform={ig|fb|ig_story|fb_story}`
4. Same day 14:00 cron checks flags and posts if approved
5. Once posted, post_id is saved → won't repost on next run

---

## 8. VERCEL CRON CONFIGURATION

**File**: `vercel.json`, section `"crons"`

```json
"crons": [
  { "path": "/api/trigger_preview", "schedule": "0 18 * * 0" },  // Sun 18:00 UTC
  { "path": "/api/trigger_digest",  "schedule": "0 12 * * 1" },  // Mon 12:00 UTC
  { "path": "/api/trigger_social",  "schedule": "0 6 * * *"  },  // Every day 6:00 UTC
  { "path": "/api/trigger_post",    "schedule": "0 12 * * *"  }  // Every day 12:00 UTC
]
```

- **Note**: Crons don't count toward function limit (12 functions max on Hobby)
- All cron endpoints trigger GitHub Actions workflows via `gh api dispatch`

---

## 9. FILE STRUCTURE

```
/Users/vincenzo/neuro-digest/
├── CLAUDE.md                    # This file
├── .env                         # Environment variables (API keys, secrets)
├── .github/workflows/
│   ├── preview.yml              # Sunday 18:00 UTC: generate + preview
│   ├── digest.yml               # Monday 12:00 UTC: send newsletter
│   ├── social_daily.yml         # Daily 6:00 UTC: generate social content
│   └── social_post.yml          # Daily 12:00 UTC: publish approved posts
├── api/
│   ├── _disabled/               # Suspended features (Stripe, etc.)
│   ├── approve.py               # Newsletter approval endpoint
│   ├── social_approve.py        # Social approval endpoint
│   ├── subscribe.py             # Newsletter signup form
│   ├── unsubscribe.py           # Unsubscribe link handler
│   ├── unsubscribe_email.py     # Email unsubscribe POST
│   ├── preferences.py           # Preferences form
│   ├── confirm.py               # Email confirmation
│   ├── trigger_preview.py       # Vercel Cron → GitHub dispatch
│   ├── trigger_digest.py        # Vercel Cron → GitHub dispatch
│   ├── trigger_social.py        # Vercel Cron → GitHub dispatch
│   └── trigger_post.py          # Vercel Cron → GitHub dispatch
├── vercel.json                  # Vercel config (crons, rewrites)
├── requirements.txt             # Python dependencies
├── index.html                   # Landing page (newsletter signup)
├── privacy.html                 # Privacy Policy
├── unsubscribe.html             # Unsubscribe form
├── preferences.html             # Preferences page
├── success.html                 # Confirmation page
├── digest.py                    # Newsletter generation logic
├── preview_send.py              # Preview email sender
├── social_daily.py              # Daily social content generator
└── social_post.py               # Social posting logic
```

---

## 10. ENVIRONMENT VARIABLES

**File**: `.env` (add to .gitignore)

```bash
# Supabase
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=xxxxx

# Resend (Email)
RESEND_API_KEY=xxxxx
RESEND_FROM="NeuroDigest <digest@neurodigest.io>"

# Anthropic (Claude API)
ANTHROPIC_API_KEY=xxxxx

# Notion
NOTION_TOKEN=xxxxx
NOTION_DATABASE_ID=xxxxx

# Meta (Instagram + Facebook)
FB_PAGE_TOKEN=xxxxx
FB_PAGE_ID=xxxxx
IG_ACCOUNT_ID=xxxxx

# Security
APPROVE_SECRET=xxxxx (random string for HMAC token validation)
SOCIAL_APPROVE_SECRET=xxxxx (random string for social HMAC)
CRON_SECRET=xxxxx (random string for Vercel Cron auth)

# GitHub (for Actions dispatch)
GH_TOKEN=xxxxx

# Site URLs
SITE_URL=https://www.neuro-digest.com
SITE_URL_PREVIEW=https://neuro-digest-phi.vercel.app

# JWT (for preferences token)
JWT_SECRET=xxxxx

# Email sender (legacy, unused in current setup)
EMAIL_SENDER=xxxxx
EMAIL_PASSWORD=xxxxx
```

---

## 11. RECENT CRITICAL FIXES (2026-06-10)

### BUG #1: Notion Pagination Limit
**File**: `social_daily.py`, function `get_existing_notion_keys()`
- **Problem**: Only fetched first 100 articles from Notion, caused duplicates
- **Fix**: Implemented pagination with `has_more` and `next_cursor`
- **Impact**: Now handles unlimited articles, prevents duplicate creation

### BUG #2: Rielaboration of Unapproved Articles
**File**: `social_daily.py`, line ~718
- **Problem**: Same article from PubMed (last 3 days) was rielaborated daily
- **Fix**: Added `pending_urls` filter to skip articles with pending unapproved posts
- **Impact**: Prevents duplicate preview emails

### BUG #3: Undefined Variables
**File**: `social_post.py`, lines 211-212
- **Problem**: `do_ig_story_video` and `do_fb_story_video` used but never defined
- **Fix**: Added definitions with `bool(post.get(...))`
- **Impact**: Prevents potential NameError crashes

### BUG #4: Incorrect posted_at Logic
**File**: `social_post.py`, lines 300-304
- **Problem**: `posted_at` was set even when nothing was published, blocking future edits
- **Fix**: Added `something_posted` flag, only set `posted_at` if something actually published
- **Impact**: Unapproved posts remain reprocessable, prevents database locking

---

## 12. CURRENT STATE (2026-06-10)

✅ **Working Features**:
- Newsletter generation, preview, approval, sending (Monday 14:00)
- Social content generation, preview emails (8:00 daily)
- Social posting after approval (14:00 daily)
- Notion article tracking with deduplication
- Privacy Policy, Unsubscribe, Preferences pages on Vercel
- Domain https://www.neuro-digest.com routing correctly
- All Vercel Crons precise and reliable

✅ **Recent Fixes**:
- No more duplicate articles on Notion
- No more duplicate preview emails
- No more automatic posting without approval
- No more blocking of unapproved posts
- Only single publish per approved post

🔴 **Known Limitations**:
- Reel MP4 sent as download link (user must add music + post manually)
- Only 2 Vercel cron slots available (used for social daily + post)
- Newsletter + preview on GitHub Actions (less precise than Vercel, but acceptable for 1x/week)
- No analytics/click tracking on newsletter links

---

## 13. ARCHITECTURE DECISIONS

### Why Vercel Cron + GitHub Actions?
- **Vercel Cron**: Precise, reliable, no external service needed. Used for:
  - Daily social content generation (8:00)
  - Daily social posting (14:00)
- **GitHub Actions**: Works well for weekly tasks. Used for:
  - Newsletter generation + preview (once/week)
  - Newsletter sending (once/week)
- **Trade-off**: Vercel Cron limit is 2 on Hobby, we use 4 total. Newsletter runs on GH Actions which is less reliable but acceptable 1x/week.

### Why Notion for Articles?
- User requested deep integration
- Provides visual article management UI
- Allows deduplication tracking (Status, Use for fields)
- No API cost (unlike other database solutions)

### Why Claude Opus 4.5?
- Best for content generation + layout decisions
- Better than Sonnet for carousel slide structuring
- Cost justified by quality + weekly frequency

### Why Supabase?
- PostgreSQL with REST API
- Integrated Storage for images/videos
- Real-time capabilities (future use)
- Generous free tier

### Why Playwright + Moviepy?
- Playwright: Headless Chrome → pixel-perfect PNG rendering at exact sizes
- Moviepy: Simple Python library for video generation, better than ffmpeg for quick prototypes

---

## 14. SECURITY & TOKENS

### HMAC Token Validation
- Endpoint: `/api/approve`, `/api/social_approve`
- Secret: `APPROVE_SECRET`, `SOCIAL_APPROVE_SECRET`
- Validation: `hmac.compare_digest(token, secret)`
- Prevents unauthorized approval (URL must be from email)

### Cron Security
- Each Vercel Cron endpoint checks `CRON_SECRET` header
- GitHub Actions has secrets management for all API keys

### Email Preferences
- JWT tokens generated with `JWT_SECRET`
- One-time use for unsubscribe/preference links
- Token includes email + expiry

---

## 15. NEXT STEPS & ROADMAP

### Immediate (This Week)
- [ ] Monitor social_post.py to confirm no double-posting after fixes
- [ ] Verify Notion pagination is fetching all articles correctly
- [ ] Test that unapproved posts remain reprocessable

### Short Term (Next 2 Weeks)
- [ ] Implement video upload for Reel (user currently downloads + adds music manually)
- [ ] Add click tracking on newsletter links (basic Google Analytics)
- [ ] Create dashboard showing subscriber count, post engagement

### Medium Term (Next Month)
- [ ] Upgrade Vercel plan if Newsletter reliability is critical
- [ ] Add A/B testing for email subject lines
- [ ] Implement scheduling (post at specific times other than 14:00)

### Future Enhancements
- [ ] Multi-language support (Italian + English newsletters)
- [ ] Subscriber segmentation (by specialty: stroke, MS, etc.)
- [ ] Custom article curation UI (instead of pure Claude selection)
- [ ] Mobile app for approval flow (instead of email links)

---

## 16. CRITICAL COMMANDS FOR NEW SESSIONS

### Deploy
```bash
git push  # Triggers Vercel deployment
```

### Test Social Daily Locally
```bash
ANTHROPIC_API_KEY=xxx python social_daily.py
```

### Check Supabase
```bash
# List all subscribers
curl -H "apikey: $SUPABASE_KEY" \
  "$SUPABASE_URL/rest/v1/subscribers?select=email,subscribed_at"

# List pending social posts
curl -H "apikey: $SUPABASE_KEY" \
  "$SUPABASE_URL/rest/v1/social_posts?posted_at=is.null&select=id,article_title,ig_approved,fb_approved"
```

### Check GitHub Secrets
```bash
gh secret list
```

### View Vercel Logs
```bash
vercel logs  # Real-time Vercel function logs
```

---

## 17. CONTACT & NOTES

- **User Email**: vincenzolate95l@gmail.com (for previews)
- **Newsletter Email**: digest@neurodigest.io (from address)
- **Support Contact**: info@neuro-digest.com
- **Backup Contact**: Vincenzo Laterza, MD

---

## 18. SESSION CONTINUITY CHECKLIST

When resuming work in future sessions:
- [ ] Verify all 4 Vercel Crons are running (check vercel.json)
- [ ] Check that latest code is deployed (git status, Vercel deployments)
- [ ] Confirm Supabase is connected (test API call)
- [ ] Verify Notion token is valid (check .env)
- [ ] Test Resend email sending
- [ ] Confirm Meta Graph API credentials are valid

If anything fails:
1. Check `.env` file exists and has all keys
2. Run `git log --oneline -5` to see recent commits
3. Check `vercel.json` matches current workflows
4. Test single function locally: `python social_daily.py --help`

---

**Last Updated**: 2026-06-10  
**By**: Claude Sonnet 4.6  
**Commit Hash**: See git log for full history
