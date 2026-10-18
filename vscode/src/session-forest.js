'use strict';
// Native and web views must keep every session visible even when parent metadata
// forms a cycle. A cycle is unavailable linkage, never permission to invent an edge.
function forest(sessions) {
  const byKey = new Map();
  for (const session of sessions) if (session && typeof session.key === 'string' && !byKey.has(session.key)) byKey.set(session.key, session);
  const parent = new Map();
  for (const session of byKey.values()) {
    if (session.parent_status !== 'linked' || !byKey.has(session.parent_key) || session.parent_key === session.key) continue;
    const seen = new Set([session.key]); let ancestor = session.parent_key; let cyclic = false;
    while (byKey.has(ancestor)) {
      if (seen.has(ancestor)) {cyclic = true; break;}
      seen.add(ancestor); const candidate = byKey.get(ancestor);
      if (candidate.parent_status !== 'linked') break;
      ancestor = candidate.parent_key;
    }
    if (!cyclic) parent.set(session.key, session.parent_key);
  }
  const children = new Map();
  for (const [child, owner] of parent) {
    if (!children.has(owner)) children.set(owner, []);
    children.get(owner).push(byKey.get(child));
  }
  return {byKey, parent, children, roots: [...byKey.values()].filter(session => !parent.has(session.key))};
}
module.exports = {forest};
