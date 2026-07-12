/**
 * Disease search via MeSH (Medical Subject Headings) — the NLM's controlled
 * vocabulary thesaurus used for indexing PubMed.
 *
 * Source: NLM MeSH REST API (https://id.nlm.nih.gov/mesh/lookup)
 * License: Public domain.
 *
 * We use MeSH because it is the single authoritative vocabulary for diseases
 * used across biomedical research. Every PubMed article is indexed against
 * MeSH descriptors.
 *
 * FE-027 ROOT FIX (Team Member 15):
 *
 * ROOT CAUSE: MeSH tree numbers are hierarchical (e.g. D03 = diseases,
 * D03.438 = nervous system diseases, D03.438.221 = epilepsy). The
 * previous code returned them as a flat `string[]` — discarding the
 * hierarchy. The frontend could not navigate diseases by category.
 *
 * ROOT FIX: in addition to the flat `treeNumber` list (kept for
 * backwards compatibility), we now build a `treeNumberHierarchy`:
 * a nested `MeshTreeNode[]` forest where each node's `children`
 * contains its descendant tree numbers. The dashboard can render
 * this as a collapsible tree.
 *
 * The hierarchy is derived purely from the dot-separated structure
 * of the tree numbers themselves — no extra API calls needed. For
 * example, given `["D03.438.221", "D03.438"]`, we build:
 *   { treeNumber: "D03", children: [
 *       { treeNumber: "D03.438", children: [
 *           { treeNumber: "D03.438.221", children: [] }
 *       ]}
 *   ]}
 *
 * Note: MeSH tree numbers may share prefixes across different
 * descriptors (e.g. D03.438 may belong to a different descriptor
 * than D03.438.221). The hierarchy built here is LOCAL to the
 * descriptor's own tree numbers — we do not cross-reference other
 * descriptors. This preserves the "where does THIS descriptor sit
 * in the MeSH tree?" semantics without conflating it with
 * "what are ALL the descriptors under D03.438?".
 */

const MESH_BASE = "https://id.nlm.nih.gov/mesh/lookup";

export interface MeshDescriptor {
  descriptorUi: string; // e.g., D000001
  name: string;
  scopeNote?: string;
  allowDuplicates: boolean;
  treeNumber: string[]; // e.g., ["C01.001"] — flat list (backwards-compat)
  /**
   * FE-027: nested tree-number hierarchy. Each root node has its
   * descendants as `children`. Built from the flat `treeNumber`
   * list by parsing the dot-separated path.
   */
  treeNumberHierarchy: MeshTreeNode[];
}

export interface MeshTreeNode {
  /** Full tree number at this node (e.g. "D03.438.221"). */
  treeNumber: string;
  /** The path segments from root to this node (e.g. ["D03","438","221"]). */
  path: string[];
  /** Child nodes (descendants of this tree number). */
  children: MeshTreeNode[];
}

/**
 * FE-027 ROOT FIX: build a nested forest of `MeshTreeNode` from a flat
 * list of tree numbers. The hierarchy is derived from the dot-separated
 * path structure.
 *
 * Example:
 *   buildTreeNumberHierarchy(["D03.438.221", "D03.438", "C01.001"])
 *   => [
 *        { treeNumber: "D03", path: ["D03"], children: [
 *            { treeNumber: "D03.438", path: ["D03","438"], children: [
 *                { treeNumber: "D03.438.221", path: ["D03","438","221"], children: [] }
 *            ]}
 *        ]},
 *        { treeNumber: "C01", path: ["C01"], children: [
 *            { treeNumber: "C01.001", path: ["C01","001"], children: [] }
 *        ]}
 *      ]
 *
 * Algorithm: for each tree number, split on "." to get the path. Walk
 * the forest from the root, creating intermediate nodes as needed, and
 * insert the leaf. This is O(N * D) where N is the number of tree
 * numbers and D is the max depth (typically ≤ 5 for MeSH).
 *
 * Exported for unit testing.
 */
export function buildTreeNumberHierarchy(treeNumbers: string[]): MeshTreeNode[] {
  const forest: MeshTreeNode[] = [];

  // Sort by tree number so parents are inserted before children
  // (e.g. "D03" before "D03.438" before "D03.438.221"). This is a
  // lexical sort — it works because the dot separator sorts before
  // any digit, so "D03" < "D03.438" < "D03.438.221".
  const sorted = [...treeNumbers].filter(Boolean).sort();

  for (const tn of sorted) {
    const segments = tn.split(".");
    let currentLevel = forest;
    let currentPath: string[] = [];

    for (let i = 0; i < segments.length; i++) {
      const segment = segments[i];
      currentPath = currentPath.concat(segment);
      const fullPath = currentPath.join(".");
      let node = currentLevel.find((n) => n.treeNumber === fullPath);
      if (!node) {
        node = {
          treeNumber: fullPath,
          path: [...currentPath],
          children: [],
        };
        currentLevel.push(node);
      }
      currentLevel = node.children;
    }
  }

  return forest;
}

/**
 * Lookup MeSH descriptors matching a free-text disease query.
 * Returns the canonical descriptor record(s) used to index that disease.
 */
export async function searchDiseasesByName(
  query: string,
  limit = 10
): Promise<MeshDescriptor[]> {
  const q = (query || "").trim();
  if (q.length < 2) return [];
  const url = `${MESH_BASE}/descriptor?label=${encodeURIComponent(q)}&limit=${limit}`;
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
    next: { revalidate: 86400 * 30 }, // MeSH updates ~weekly
  });
  if (!res.ok) {
    throw new Error(`MeSH descriptor lookup returned ${res.status}`);
  }
  const uris: string[] = await res.json();
  if (uris.length === 0) return [];
  const descriptors: MeshDescriptor[] = [];
  for (const uri of uris.slice(0, limit)) {
    const descriptorUi = uri.split("/").pop() || "";
    if (!descriptorUi) continue;
    // Fetch the name and scope note
    const nameRes = await fetch(
      `${MESH_BASE}/descriptor?resource=${encodeURIComponent(uri)}`,
      {
        headers: { Accept: "application/json" },
        next: { revalidate: 86400 * 30 },
      }
    );
    if (!nameRes.ok) continue;
    const name = (await nameRes.text()).trim().replace(/^"|"$/g, "");
    let scopeNote: string | undefined;
    try {
      const snRes = await fetch(
        `${MESH_BASE}/scopeNote?resource=${encodeURIComponent(uri)}`,
        {
          headers: { Accept: "application/json" },
          next: { revalidate: 86400 * 30 },
        }
      );
      if (snRes.ok) scopeNote = (await snRes.text()).trim().replace(/^"|"$/g, "");
    } catch {}
    let treeNumber: string[] = [];
    try {
      const tnRes = await fetch(
        `${MESH_BASE}/treeNumber?resource=${encodeURIComponent(uri)}`,
        {
          headers: { Accept: "application/json" },
          next: { revalidate: 86400 * 30 },
        }
      );
      if (tnRes.ok) treeNumber = await tnRes.json();
    } catch {}
    descriptors.push({
      descriptorUi,
      name,
      scopeNote,
      allowDuplicates: false,
      treeNumber,
      // FE-027: build the nested hierarchy from the flat list.
      treeNumberHierarchy: buildTreeNumberHierarchy(treeNumber),
    });
  }
  return descriptors;
}
