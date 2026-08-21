"""
Sources package — each module here knows how to fetch postings from one place.

Right now there's one source (the Summer 2027 repo). When you graduate and want
New-Grad-Positions too, you add ONE new file here that returns Posting objects,
register it in refresh.py, and you're done. Scoring, storage, and the dashboard
need no changes — they only ever see Posting objects and don't care where those
came from.
"""

from .base import Posting, Source
from .simplify_readme import SimplifyReadmeSource

__all__ = ["Posting", "Source", "SimplifyReadmeSource"]
