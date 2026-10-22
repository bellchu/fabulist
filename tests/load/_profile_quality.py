import time, tracemalloc, sys
from app import quality
n = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
def stmts(n, templated):
    out = []
    for i in range(n):
        if templated:
            out.append("The system shall allow an authorised %s user to process request number %d, validate it against the business rules for area %d, record who changed what and when, and notify the requester within %d seconds." % (["buyer", "approver", "auditor", "planner"][i % 4], i, i % 17, 5 + i % 55))
        else:
            out.append("The system shall support capability %d: %s %s %s" % (i, *["alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega".split()[(i * k) % 24] for k in (3, 7, 11)]))
    return out
for templated in (True, False):
    items = [{"id": str(i), "key": "REQ-%d" % i, "statement": s, "status": "draft"} for i, s in enumerate(stmts(n, templated))]
    tracemalloc.start()
    t = time.time()
    pairs = quality.duplicate_pairs(items)
    took = time.time() - t
    cur, peak = tracemalloc.get_traced_memory(); tracemalloc.stop()
    print("n=%d templated=%s: duplicate_pairs %.2f s, %d pairs, peak %.0f MB" % (n, templated, took, len(pairs), peak / 1048576))
