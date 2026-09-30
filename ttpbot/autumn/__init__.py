"""The Autumn Tournament's half of the race night.

Beside `ttpbot.league` rather than inside it. The two share the shape of the
problem -- read a schedule, open a room at T-30, wake a booth at T-35 -- and
almost none of the specifics: the League has fixtures, weeks, teams and
home/away, and the tournament has a bracket, byes, a losers side and a final
that may need playing twice.

What the tournament does *not* have is authority over its own times. The
Schedule tab owns those; the bracket engine owns match identity, games, results
and advancement. This package reads the first and reports to the second.
"""
