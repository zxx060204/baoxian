"""Font-scaled native text tolerances for tightly spaced financial documents."""
from statistics import median


def text_tolerances(page):
    heights=[c['height'] for c in getattr(page,'chars',[]) if c.get('height',0)>0 and c.get('text','').strip()]
    if not heights:
        return 2, 3
    height=median(heights)
    return max(.2,min(2,height*.15)), max(.2,min(3,height*.2))
