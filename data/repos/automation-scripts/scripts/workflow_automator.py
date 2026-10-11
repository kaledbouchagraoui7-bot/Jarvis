def summarize_workflows(rows):
    """Return count of rows per database (deterministic, no guessing)."""
    out = {}
    for r in rows:
        db = r.get('database', 'unknown')
        out[db] = out.get(db, 0) + 1
    return out

if __name__ == '__main__':
    print(summarize_workflows([{'database': 'ops'}, {'database': 'ops'}, {'database': 'crm'}]))