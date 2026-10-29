/**
 * SAMPLE DATA for the preview UI — every placeholder in the app lives here.
 *
 * Nothing in this file is about the signed-in user or comes from the API. It is
 * shown only with preview features on (lib/preview.ts), always next to a
 * "Preview" label, and each export is replaced by real data when its backend
 * exists. Search for an import of this file to find everything still waiting.
 */

/** Troubleshooter: one worked example. */
export const HOMEWORK_EXAMPLE = {
  source: "Cisco IOS · show interfaces",
  question: "GigabitEthernet0/1 is up, line protocol is down after the uplink cable was replaced.",
  steps: [
    {
      title: "Step 1: Check layer 1 and 2",
      note: "Interface up but line protocol down points at cabling, optics or a speed/duplex mismatch.",
      work: ["show interfaces gi0/1 | include duplex|CRC", "Full-duplex, 1000Mb/s · 2290 CRC"],
    },
    { title: "Step 2: Match the far end", note: null, work: ["interface gi0/1", "speed auto", "duplex auto"] },
  ],
  answer: "Set speed and duplex to auto on both ends (or the same fixed values); replace the cable if CRC errors continue.",
} as const;

/** Summarizer: one summary. */
export const SUMMARY_EXAMPLE = {
  title: "Key Summary: OSPF adjacency troubleshooting",
  readTime: "2 Min Read",
  points: [
    {
      term: "Neighbor states",
      text: "Down, Init, 2-Way, ExStart, Exchange, Loading, Full. Stuck in ExStart or Exchange usually means an MTU mismatch.",
    },
    {
      term: "Must match",
      text: "Area ID, subnet and mask, hello and dead timers, authentication and stub flags must agree on both ends.",
    },
    {
      term: "Router ID",
      text: "Duplicate router IDs stop adjacencies forming and make LSAs flap; set them explicitly.",
    },
    { term: "Useful commands", text: "show ip ospf neighbor, show ip ospf interface, debug ip ospf adj." },
  ],
} as const;
