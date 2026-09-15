"""
General vocabulary of the tech job market: skills, role families, seniority
markers, abbreviations, résumé section names.

Everything here describes the WORLD, not a user. Which of these a particular
user has, wants, or is qualified for is derived per profile in
`jobrank/profile/derive.py`. Nothing in this file carries a preference weight.

Matching is whole-word via `jobrank.textmatch`, so an alias like "go" does not
fire inside "google". Aliases that are ordinary English words when lowercased
are listed under `case_sensitive` and only match with their original casing.
"""

# -----------------------------------------------------------------------------
# Skills: canonical name -> aliases and category.
# -----------------------------------------------------------------------------
# `category` groups skills for display and for role-family evidence; it is not
# a weight. Canonical names are what profiles and postings store.

SKILLS = {
    # Languages
    "python": {"aliases": ["python", "python3"], "category": "language"},
    "java": {"aliases": ["java"], "category": "language"},
    "javascript": {"aliases": ["javascript", "js", "ecmascript"], "category": "language"},
    "typescript": {"aliases": ["typescript", "ts"], "category": "language"},
    "c": {"aliases": [], "case_sensitive": ["C"], "category": "language"},
    "c++": {"aliases": ["c++", "cpp"], "category": "language"},
    "c#": {"aliases": ["c#", "csharp"], "category": "language"},
    "go": {"aliases": ["golang"], "case_sensitive": ["Go"], "category": "language"},
    "rust": {"aliases": ["rust"], "category": "language"},
    "ruby": {"aliases": ["ruby"], "category": "language"},
    "php": {"aliases": ["php"], "category": "language"},
    "swift": {"aliases": ["swift"], "category": "language"},
    "kotlin": {"aliases": ["kotlin"], "category": "language"},
    "scala": {"aliases": ["scala"], "category": "language"},
    "r": {"aliases": [], "case_sensitive": ["R"], "category": "language"},
    "sql": {"aliases": ["sql"], "category": "language"},
    "matlab": {"aliases": ["matlab"], "category": "language"},
    "bash": {"aliases": ["bash", "shell scripting", "zsh"], "category": "language"},
    "verilog": {"aliases": ["verilog", "systemverilog", "vhdl"], "category": "language"},
    "html": {"aliases": ["html", "html5"], "category": "language"},
    "css": {"aliases": ["css", "css3", "sass", "scss"], "category": "language"},

    # Frontend / mobile
    "react": {"aliases": ["react", "react.js", "reactjs"], "category": "frontend"},
    "angular": {"aliases": ["angular", "angularjs", "angular.js"], "category": "frontend"},
    "vue": {"aliases": ["vue", "vue.js", "vuejs"], "category": "frontend"},
    "next.js": {"aliases": ["next.js", "nextjs"], "category": "frontend"},
    "tailwind": {"aliases": ["tailwind", "tailwindcss"], "category": "frontend"},
    "react native": {"aliases": ["react native"], "category": "mobile"},
    "flutter": {"aliases": ["flutter"], "category": "mobile"},
    "ios": {"aliases": ["ios", "swiftui", "uikit"], "category": "mobile"},
    "android": {"aliases": ["android", "jetpack compose"], "category": "mobile"},

    # Backend
    "node.js": {"aliases": ["node.js", "nodejs", "node"], "category": "backend"},
    "express": {"aliases": ["express", "express.js"], "category": "backend"},
    "django": {"aliases": ["django"], "category": "backend"},
    "flask": {"aliases": ["flask"], "category": "backend"},
    "fastapi": {"aliases": ["fastapi"], "category": "backend"},
    "spring": {"aliases": ["spring", "spring boot"], "category": "backend"},
    ".net": {"aliases": [".net", "asp.net", "dotnet"], "category": "backend"},
    "rails": {"aliases": ["rails", "ruby on rails"], "category": "backend"},
    "graphql": {"aliases": ["graphql"], "category": "backend"},
    "rest api": {"aliases": ["rest", "restful", "rest api", "rest apis"], "category": "backend"},
    "grpc": {"aliases": ["grpc", "protobuf"], "category": "backend"},
    "microservices": {"aliases": ["microservices", "microservice"], "category": "backend"},

    # Data stores and data engineering
    "postgresql": {"aliases": ["postgresql", "postgres"], "category": "data"},
    "mysql": {"aliases": ["mysql"], "category": "data"},
    "mongodb": {"aliases": ["mongodb", "mongo"], "category": "data"},
    "redis": {"aliases": ["redis"], "category": "data"},
    "elasticsearch": {"aliases": ["elasticsearch", "opensearch"], "category": "data"},
    "dynamodb": {"aliases": ["dynamodb"], "category": "data"},
    "snowflake": {"aliases": ["snowflake"], "category": "data"},
    "bigquery": {"aliases": ["bigquery"], "category": "data"},
    "spark": {"aliases": ["spark", "pyspark", "apache spark"], "category": "data"},
    "kafka": {"aliases": ["kafka", "apache kafka"], "category": "data"},
    "airflow": {"aliases": ["airflow", "apache airflow"], "category": "data"},
    "dbt": {"aliases": ["dbt"], "category": "data"},
    "etl": {"aliases": ["etl", "elt", "data pipeline", "data pipelines"], "category": "data"},
    "pandas": {"aliases": ["pandas"], "category": "data"},
    "numpy": {"aliases": ["numpy"], "category": "data"},
    "tableau": {"aliases": ["tableau", "power bi", "looker"], "category": "data"},

    # Cloud and infrastructure
    "aws": {"aliases": ["aws", "amazon web services", "ec2", "s3", "lambda"], "category": "cloud"},
    "gcp": {"aliases": ["gcp", "google cloud", "google cloud platform"], "category": "cloud"},
    "azure": {"aliases": ["azure", "microsoft azure"], "category": "cloud"},
    "docker": {"aliases": ["docker", "containers", "containerization"], "category": "infra"},
    "kubernetes": {"aliases": ["kubernetes", "k8s", "helm"], "category": "infra"},
    "terraform": {"aliases": ["terraform", "infrastructure as code", "pulumi"], "category": "infra"},
    "ci/cd": {"aliases": ["ci/cd", "github actions", "jenkins", "gitlab ci", "circleci"], "category": "infra"},
    "linux": {"aliases": ["linux", "unix"], "category": "infra"},
    "git": {"aliases": ["git", "github", "gitlab"], "category": "infra"},
    "observability": {"aliases": ["datadog", "prometheus", "grafana", "opentelemetry", "observability"], "category": "infra"},
    "distributed systems": {"aliases": ["distributed systems", "distributed system"], "category": "infra"},

    # ML / AI
    "machine learning": {"aliases": ["machine learning", "ml"], "category": "ml"},
    "deep learning": {"aliases": ["deep learning", "neural networks", "neural network"], "category": "ml"},
    "pytorch": {"aliases": ["pytorch", "torch"], "category": "ml"},
    "tensorflow": {"aliases": ["tensorflow", "keras"], "category": "ml"},
    "scikit-learn": {"aliases": ["scikit-learn", "sklearn"], "category": "ml"},
    "computer vision": {"aliases": ["computer vision", "opencv", "image recognition"], "category": "ml"},
    "nlp": {"aliases": ["nlp", "natural language processing"], "category": "ml"},
    "llm": {"aliases": ["llm", "llms", "large language model", "large language models", "generative ai", "genai"], "category": "ai"},
    "langchain": {"aliases": ["langchain", "langgraph", "llamaindex"], "category": "ai"},
    "rag": {"aliases": ["rag", "retrieval augmented generation", "retrieval-augmented generation"], "category": "ai"},
    "embeddings": {"aliases": ["embeddings", "vector database", "vector search", "pinecone", "faiss"], "category": "ai"},
    "llm apis": {"aliases": ["openai api", "anthropic api", "claude api", "gpt-4", "openai"], "category": "ai"},
    "ai agents": {"aliases": ["ai agents", "agentic", "agents", "tool use"], "category": "ai"},
    "mlops": {"aliases": ["mlops", "mlflow", "kubeflow", "model serving"], "category": "ml"},
    "cuda": {"aliases": ["cuda", "gpu programming"], "category": "ml"},

    # Security / systems / hardware
    "security": {"aliases": ["cybersecurity", "application security", "penetration testing", "threat modeling"], "category": "security"},
    "networking": {"aliases": ["networking", "tcp/ip", "dns"], "category": "infra"},
    "embedded systems": {"aliases": ["embedded systems", "embedded", "firmware", "rtos", "microcontrollers"], "category": "hardware"},
    "fpga": {"aliases": ["fpga", "asic"], "category": "hardware"},

    # Practices
    "testing": {"aliases": ["unit testing", "pytest", "jest", "integration testing", "test automation", "selenium", "playwright"], "category": "practice"},
    "agile": {"aliases": ["agile", "scrum"], "category": "practice"},
    "system design": {"aliases": ["system design", "systems design"], "category": "practice"},
}


# -----------------------------------------------------------------------------
# Role families
# -----------------------------------------------------------------------------
# `titles`   phrases that name the job in a title ("software engineer").
# `evidence` skills (canonical names above) or topic phrases whose presence in
#            résumé bullets or project descriptions indicates past work in the
#            family. Used to DERIVE a user's affinity; never a user weight.

ROLE_FAMILIES = {
    "software_engineering": {
        "label": "Software Engineering",
        "titles": ["software engineer", "software engineering", "software developer",
                   "software development engineer", "sde", "swe", "developer",
                   "programmer", "application engineer", "applications engineer",
                   "product engineer", "engineering", "engineer"],
        "evidence": ["system design", "rest api", "microservices", "testing", "git"],
    },
    "backend": {
        "label": "Backend",
        "titles": ["backend", "back-end", "back end", "server engineer", "api engineer"],
        "evidence": ["node.js", "django", "flask", "fastapi", "spring", "postgresql",
                     "rest api", "graphql", "grpc", "microservices", "redis"],
    },
    "frontend": {
        "label": "Frontend",
        "titles": ["frontend", "front-end", "front end", "ui engineer", "web developer",
                   "web engineer"],
        "evidence": ["react", "angular", "vue", "next.js", "css", "html", "typescript",
                     "tailwind"],
    },
    "fullstack": {
        "label": "Full Stack",
        "titles": ["full stack", "full-stack", "fullstack"],
        "evidence": ["react", "node.js", "postgresql", "rest api", "next.js"],
    },
    "mobile": {
        "label": "Mobile",
        "titles": ["ios", "android", "mobile engineer", "mobile developer", "mobile"],
        "evidence": ["ios", "android", "swift", "kotlin", "react native", "flutter"],
    },
    "ai_engineering": {
        "label": "AI Engineering",
        "titles": ["ai engineer", "applied ai", "ai software engineer", "llm engineer",
                   "generative ai", "genai", "ai agent", "agentic", "ai applications"],
        "evidence": ["llm", "langchain", "rag", "embeddings", "llm apis", "ai agents"],
    },
    "ml_engineering": {
        "label": "Machine Learning Engineering",
        "titles": ["machine learning engineer", "machine learning", "ml engineer", "mle",
                   "ml platform", "ml infrastructure", "deep learning", "computer vision",
                   "nlp engineer"],
        "evidence": ["machine learning", "pytorch", "tensorflow", "deep learning",
                     "computer vision", "nlp", "mlops", "cuda", "scikit-learn"],
    },
    "data_science": {
        "label": "Data Science & Analytics",
        "titles": ["data scientist", "data science", "data analyst", "analytics",
                   "business intelligence", "business analyst", "quantitative analyst"],
        "evidence": ["pandas", "r", "tableau", "scikit-learn", "sql", "statistics",
                     "a/b testing"],
    },
    "data_engineering": {
        "label": "Data Engineering",
        "titles": ["data engineer", "data engineering", "data platform", "analytics engineer"],
        "evidence": ["spark", "kafka", "airflow", "dbt", "etl", "snowflake", "bigquery"],
    },
    "infrastructure": {
        "label": "Infrastructure, Cloud & SRE",
        "titles": ["infrastructure", "platform engineer", "devops", "site reliability",
                   "sre", "cloud engineer", "systems engineer", "distributed systems",
                   "developer productivity", "production engineer"],
        "evidence": ["kubernetes", "docker", "terraform", "aws", "gcp", "azure", "ci/cd",
                     "linux", "observability", "distributed systems"],
    },
    "security": {
        "label": "Security",
        "titles": ["security", "cybersecurity", "appsec", "penetration tester",
                   "security engineer"],
        "evidence": ["security", "networking"],
    },
    "embedded_hardware": {
        "label": "Embedded & Hardware",
        "titles": ["embedded", "firmware", "hardware", "fpga", "asic", "electrical engineer",
                   "silicon", "rtl"],
        "evidence": ["embedded systems", "fpga", "verilog", "c", "c++"],
    },
    "forward_deployed": {
        "label": "Forward Deployed & Solutions",
        "titles": ["forward deployed", "fde", "solutions engineer", "sales engineer",
                   "customer engineer", "implementation engineer", "technical consultant",
                   "solutions architect", "field engineer"],
        "evidence": ["customer-facing", "client", "customers", "stakeholders",
                     "implementation", "integration"],
    },
    "product_management": {
        "label": "Product & Program Management",
        "titles": ["product manager", "product management", "apm", "program manager",
                   "technical program manager", "tpm", "product owner"],
        "evidence": ["roadmap", "product requirements", "user research", "stakeholders"],
    },
    "research": {
        "label": "Research",
        "titles": ["research scientist", "research engineer", "researcher",
                   "research intern", "applied scientist"],
        "evidence": ["publication", "paper", "research", "arxiv"],
    },
    "quant": {
        "label": "Quantitative Finance",
        "titles": ["quantitative", "quant", "trader", "trading", "quantitative developer"],
        "evidence": ["trading", "derivatives", "stochastic", "probability"],
    },
    "qa_test": {
        "label": "QA & Test",
        "titles": ["qa", "quality assurance", "test engineer", "sdet", "quality engineer"],
        "evidence": ["testing"],
    },
    "it_support": {
        "label": "IT & Support",
        "titles": ["it support", "help desk", "technical support", "it technician",
                   "systems administrator", "it intern", "desktop support"],
        "evidence": ["active directory", "troubleshooting", "ticketing"],
    },
}

# When several families match a title, the most specific wins. A generic family
# ("engineer") should not outrank "machine learning engineer".
GENERIC_FAMILIES = ["software_engineering"]


# -----------------------------------------------------------------------------
# Seniority
# -----------------------------------------------------------------------------
# Ordered least to most senior. Distances between these indexes drive the
# seniority-fit factor.
SENIORITY_LEVELS = ["intern", "entry", "mid", "senior", "staff"]

# Title markers that name a level. Checked most senior first, so "Senior
# Software Engineer Intern" (it exists) resolves by the intern rule below.
SENIORITY_TITLE_MARKERS = {
    "staff": ["staff", "principal", "distinguished", "director", "head of", "vp"],
    "senior": ["senior", "sr", "lead", "iii", "engineering manager"],
    "mid": ["ii", "mid-level", "mid level", "intermediate"],
    "entry": ["new grad", "new graduate", "entry level", "entry-level", "junior", "jr",
              "university graduate", "early career", "graduate", "associate"],
    "intern": ["intern", "internship", "co-op", "coop", "apprentice", "apprenticeship",
               "summer analyst", "fellowship"],
}
# Intern markers override every other level: an intern role is an intern role.
SENIORITY_OVERRIDE_LEVEL = "intern"

# Words that mean an experience entry was part-time (credited like an internship).
PART_TIME_TITLE_MARKERS = ["part-time", "part time", "student worker", "student employee",
                           "work-study", "work study"]

# Words that mean an experience entry on a résumé was an internship.
INTERNSHIP_TITLE_MARKERS = ["intern", "internship", "co-op", "coop", "apprentice",
                            "summer analyst", "research assistant", "teaching assistant"]


# -----------------------------------------------------------------------------
# Abbreviations expanded before comparing titles (dedupe, affinity, embeddings).
# -----------------------------------------------------------------------------
TITLE_ABBREVIATIONS = {
    "sde": "software development engineer",
    "swe": "software engineer",
    "sr": "senior",
    "jr": "junior",
    "ml": "machine learning",
    "mle": "machine learning engineer",
    "ai": "artificial intelligence",
    "sre": "site reliability engineer",
    "qa": "quality assurance",
    "sdet": "software development engineer in test",
    "fde": "forward deployed engineer",
    "pm": "product manager",
    "apm": "associate product manager",
    "tpm": "technical program manager",
    "ui": "user interface",
    "ux": "user experience",
    "eng": "engineer",
    "engr": "engineer",
    "dev": "developer",
    "devops": "devops",
    "infra": "infrastructure",
    "ds": "data scientist",
    "de": "data engineer",
}


# -----------------------------------------------------------------------------
# Industries: used for "industries to exclude". Company-name hints are in
# config/companies.py; these are the valid names a user can pick.
# -----------------------------------------------------------------------------
INDUSTRIES = [
    "software", "ai", "fintech", "finance", "quant_trading", "healthcare", "pharma",
    "biotech", "defense", "aerospace", "automotive", "energy", "manufacturing",
    "retail", "consulting", "government", "education", "media", "gaming",
    "telecom", "insurance", "real_estate", "crypto", "semiconductors", "logistics",
]


# -----------------------------------------------------------------------------
# Résumé structure
# -----------------------------------------------------------------------------
RESUME_SECTIONS = {
    "education": ["education", "academic background", "academics"],
    "experience": ["experience", "work experience", "professional experience",
                   "employment", "employment history", "work history",
                   "relevant experience", "industry experience", "internships"],
    "projects": ["projects", "personal projects", "selected projects",
                 "technical projects", "academic projects", "project experience"],
    "skills": ["skills", "technical skills", "technologies", "tech stack",
               "skills & technologies", "skills and technologies", "tools"],
    "other": ["awards", "honors", "certifications", "publications", "leadership",
              "activities", "volunteering", "interests", "coursework",
              "relevant coursework", "notes for the letter writer", "summary",
              "objective", "profile"],
}

# Words that, in an entry header, mark the half that is the job TITLE rather
# than the organization ("Acme Corp — Software Engineer Intern").
TITLE_HEAD_NOUNS = ["engineer", "intern", "developer", "analyst", "scientist",
                    "manager", "researcher", "assistant", "consultant", "lead",
                    "designer", "specialist", "technician", "associate", "fellow",
                    "architect", "administrator", "founder", "co-founder", "officer",
                    "tutor", "instructor", "programmer", "contractor"]

DEGREE_PATTERNS = {
    "phd": ["ph.d", "ph.d.", "phd", "doctorate", "doctor of philosophy"],
    "master": ["m.s.", "m.s", "ms", "msc", "m.sc", "master", "masters", "master's",
               "m.eng", "meng", "mba", "m.a.", "mcs"],
    "bachelor": ["b.s.", "b.s", "bs", "bsc", "b.sc", "bachelor", "bachelors",
                 "bachelor's", "b.a.", "ba", "b.eng", "beng", "bse", "b.s.e."],
    "associate": ["associate of", "a.s.", "a.a.", "associate degree"],
}
DEGREE_ORDER = ["none", "associate", "bachelor", "master", "phd"]

INSTITUTION_MARKERS = ["university", "college", "institute", "school", "polytechnic",
                       "academy"]
