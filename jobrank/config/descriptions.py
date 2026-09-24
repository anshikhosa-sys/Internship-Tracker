"""
Where a job description can be fetched from, and how hard to try.

Data only. Every endpoint here is a public, unauthenticated URL that the job
board already serves to any browser — the same class of request as downloading
a GitHub README. No key, no account, no billing relationship (invariant 6).

Boards are listed in the order we prefer them: a board-level endpoint returns
every posting for an employer in ONE request, so it is both faster and far
politer than fetching each posting separately.
"""

# Vendor id -> how to recognise it and where to ask.
# `board_api` takes a board token and returns every posting at once.
# `job_api` takes (board, job_id) and returns one posting.
VENDORS = {
    "greenhouse": {
        "url_pattern": r"(?:job-boards|boards)\.greenhouse\.io/(?:embed/job_app\?for=)?([\w.-]+)/jobs/(\d+)",
        "board_api": "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true",
        "job_api": "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}",
        "content_keys": ["content"],
    },
    "lever": {
        "url_pattern": r"jobs\.lever\.co/([\w.-]+)/([\w-]{36})",
        "board_api": "https://api.lever.co/v0/postings/{board}?mode=json",
        "job_api": "https://api.lever.co/v0/postings/{board}/{job}",
        "content_keys": ["descriptionPlain", "description"],
    },
    "ashby": {
        "url_pattern": r"jobs\.ashbyhq\.com/([\w.-]+)/([\w-]{36})",
        "board_api": "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true",
        "job_api": None,          # the per-job endpoint requires a key; the board one does not
        "content_keys": ["descriptionPlain", "description"],
    },
    "smartrecruiters": {
        "url_pattern": r"jobs\.smartrecruiters\.com/([\w.-]+)/(\d+)",
        "board_api": None,
        "job_api": "https://api.smartrecruiters.com/v1/companies/{board}/postings/{job}",
        "content_keys": ["jobAd"],
    },
    "workday": {
        # Workday renders its pages in JavaScript, but every tenant exposes the
        # same JSON behind /wday/cxs/. Matches the tenant, data centre, site and
        # job path in one go.
        "url_pattern": r"https://([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[\w-]+/)?([\w-]+)(/job/[^?#]+)",
        "board_api": None,
        "job_api": None,          # built by hand: the path itself carries the host
        "content_keys": ["jobDescription"],
    },
    "workable": {
        "url_pattern": r"apply\.workable\.com/([\w.-]+)/j/(\w+)",
        "board_api": None,
        "job_api": "https://apply.workable.com/api/v1/widget/accounts/{board}?job={job}",
        "content_keys": ["description"],
    },
}

# zapply.jobs is a redirector whose own path names the vendor and board it
# points at:  /l/d/lever-ifm-us-<uuid>  ->  lever, board "ifm-us", that uuid.
# Decoding the slug saves a redirect per posting across ~1,800 of them.
REDIRECT_SLUG_PATTERN = r"zapply\.jobs/l/d/([a-z]+)-(.+?)-([\w-]{36}|\d{4,})(?:\?|$)"
# The slug's vendor word is not always the vendor's own name.
REDIRECT_VENDOR_ALIASES = {"sr": "smartrecruiters", "gh": "greenhouse"}

# dreamworkhq.com serves its own page but links out to the real board; a
# Greenhouse id arrives as ?gh_jid=. Cheapest recognisable outbound markers.
PAGE_OUTBOUND_PATTERNS = [
    (r"gh_jid=(\d+)", "greenhouse_jid"),
    (r"(?:job-boards|boards)\.greenhouse\.io/([\w.-]+)/jobs/(\d+)", "greenhouse"),
    (r"jobs\.lever\.co/([\w.-]+)/([\w-]{36})", "lever"),
    (r"jobs\.ashbyhq\.com/([\w.-]+)/([\w-]{36})", "ashby"),
]
PAGE_HOSTS = ["dreamworkhq.com"]

# Politeness and safety.
REQUEST_TIMEOUT_SECONDS = 20
DELAY_BETWEEN_REQUESTS_SECONDS = 0.35
MAX_ATTEMPTS_PER_HOST = 3          # consecutive failures before a host is skipped
USER_AGENT = "jobrank (+https://github.com/anshikhosa-sys/Internship-Tracker)"

# A description shorter than this is boilerplate ("Apply here"), not content.
MIN_DESCRIPTION_CHARS = 200
# Descriptions are stored trimmed; the tail is legal boilerplate (EEO, benefits)
# that is identical across postings and would dilute every similarity score.
MAX_DESCRIPTION_CHARS = 12000
