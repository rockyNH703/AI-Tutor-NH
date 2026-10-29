"""The assistant's intents: the one action a user's latest message asks NetSec AI to perform now.

The owner's 46 criteria (2026-10-22), adapted on 2026-10-29 for NetSec AI, the assistant for network,
security and infrastructure engineers and for certification and lab practice (the owner's decision:
modify, don't delete). The ids, groups and mechanics are the owner's; `criterion` is the long text
(the docs and scripts/eval_intents.py), and each intent has a fixed `option` of at most 80
characters that keeps the criterion's key distinction: the text the classification model reads
(app/intents/classifier.py). Long option texts cost time on every request (2026-10-22, warm: one
question with nine short options 260 ms, with nine long ones 635 ms), so the app never sends the
criteria. The ids and texts change only with a new TAXONOMY_VERSION and a new run of
scripts/eval_intents.py (the 2026-10-29 texts need new cases and a new --sweep before the router is
turned on). Pure data, no I/O.

- **Two stages:** a small model chooses better among a few options, and one question per request
  is fast, so the model is asked for one of eight groups (or `other`: greetings, thanks, chit-chat,
  nothing about engineering), then, in a second request, for the intent within that group. An intent
  scores P(its group) x P(it | its group).
- **No other labels:** a follow-up ("why?", "make it shorter") gets the intent of what it asks for,
  read with the message before it; `other` is never an intent; a message too vague to read gets none.
  No intent means the assistant as it was before the router (it asks a short question when it must).
- **What an intent changes** (app/chat/assistant.py): `guidance` is one line telling the assistant
  what the message asks for ("": its own way is right); `retrieval` False skips the knowledge search
  (the answer is in the app's own records); `bank` gives the assistant the question bank's domains, as
  if the message's words had asked for a test (app/qbank/chat_tests.py).
"""

from __future__ import annotations

from dataclasses import dataclass

TAXONOMY_VERSION = "2026-10-29"
ABSTAIN = "other"  # an option of the group question only, never an intent


@dataclass(frozen=True)
class Intent:
    group: str
    option: str  # what the classifier reads: at most 80 characters, from the criterion
    criterion: str  # the long text (never sent by the app)
    guidance: str = ""  # what the assistant is told on a turn with this intent ("": nothing)
    retrieval: bool = True  # False: no knowledge search (the answer is in the app's own records)
    bank: bool = False  # a test request: the assistant is given the question bank's domains


# The group question's options: one discriminative line each, at most 80 characters.
GROUPS: dict[str, str] = {
    "understand": "Understand a concept: explain, define, why or how, use cases, compare, myths",
    "solve": "Solve a task: troubleshoot, configure, design, subnet or calculate, a lab task",
    "feedback": "Check or debug their own answer, config or work; exam scoring; or only a hint",
    "materials": "Reference material: docs lookup, summary, notes, cheat sheet, flashcards, map",
    "language": "Translate, simplify or reformat given text, keeping its meaning",
    "tests": "Certification practice: MCQs, quiz, practice exam, harder questions, exam prep",
    "planning": "Analyse their own practice results or progress, or make a study or lab plan",
    "visual_lab": "A diagram, chart or screenshot question; building or running a lab; a project",
    ABSTAIN: "A greeting, thanks, chit-chat, or anything not about engineering or IT work",
}

# Both questions end by saying which line is the request: context lines are never instructions.
GROUP_QUESTION = (
    "What does the user want NetSec AI to do now? Choose the kind of help their current message asks for: the "
    "action, not the technology or the language it is written in. Follow explicit constraints such as hint-only. "
    "The assistant's words are never the user's request. Only the line starting 'Current message' is the "
    "user's request; any earlier lines are context only."
)
# The owner's instruction for the decision model, for the question within each group.
INTENT_QUESTION = (
    "Choose exactly one category for the user's primary requested action in the current message. Use the "
    "conversation context when available. Follow explicit constraints such as hint-only or step-by-step. Choose "
    "the most specific matching category. Classify the task, not just the technology or language. Only the line "
    "starting 'Current message' is the user's request; any earlier lines are context only."
)

# In the owner's order, which also breaks ties.
INTENTS: dict[str, Intent] = {
    "explain_concept": Intent(
        "understand",
        "Explain a protocol, technology or concept from the basics, not just define it",
        "Explain or clarify a networking, security or infrastructure concept from the basics. The user wants to "
        "understand an idea, protocol or mechanism, not merely receive a definition. Example: “OSPF ko basics se "
        "samjhao.”",
    ),
    "define_term": Intent(
        "understand",
        "Give the meaning or a short definition of a term, acronym, port or keyword",
        "Give the meaning or a concise definition of a term, acronym, port number, header field or command "
        "keyword. Example: “What is a VRF?”",
        "Give a precise definition in one or two sentences and one short example (a command, a port or a "
        "packet field where it fits); keep the reply short.",
    ),
    "why_how": Intent(
        "understand",
        "Explain why or how a protocol or system behaves as it does: cause or mechanism",
        "Explain the cause, reason or mechanism behind how a protocol, device or system behaves. The main request "
        "is “why” or “how,” not a configuration. Example: “Why does STP block a port?”",
        "Explain the reason or the mechanism step by step, from cause to effect, at the packet or state level "
        "where that helps.",
    ),
    "real_life_application": Intent(
        "understand",
        "Show where a technology is used in real networks, products or attacks",
        "Show where or how a technology is used in real networks, data centers, cloud platforms, products or "
        "real attacks and defences. Example: “Where is VXLAN actually used?”",
        "Show where this is used in real deployments: two or three concrete scenarios, each tied back to the "
        "concept and why it was chosen there.",
    ),
    "analogy_example": Intent(
        "understand",
        "Explain through an analogy, a comparison of alternatives or a simple example",
        "Explain a topic using an analogy, a comparison of alternatives (one technology against another), or a "
        "simple example specifically requested by the user. Example: “Explain NAT with a simple analogy.”",
        "Explain it through one clear analogy, a side-by-side comparison (a table when comparing technologies) or "
        "a worked example, then say where the analogy stops fitting.",
    ),
    "prerequisite_learning": Intent(
        "understand",
        "Cover the fundamentals the user says they lack before learning a topic",
        "Cover prerequisite or foundational knowledge the user says they lack and need before the target topic. "
        "Example: “Subnetting nahi aata; BGP kaise seekhun?”",
        "List what they need to know first, in order, and briefly explain the first item.",
    ),
    "deeper_learning": Intent(
        "understand",
        "Go deeper: internals, RFC-level detail, edge cases, beyond the basics",
        "Explore a topic beyond the usual explanation: internals, RFC-level behaviour, timers, edge cases or "
        "related curiosity. Use when depth is the main request, not a simpler explanation. Example: “What really "
        "happens inside a TCP handshake under SYN cookies?”",
        "Go beyond the usual explanation: the internals and reasoning behind it, its limits and edge cases, and "
        "what it leads to; cite the RFC or vendor documentation only when the reference material shows it.",
    ),
    "misconception_check": Intent(
        "understand",
        "Check whether a belief or claim is true and correct the misconception",
        "Check or correct a belief or claim the user presents as possibly true or false. Explain the "
        "misconception and the accurate idea. Example: “NAT is a security feature, right?”",
        "Say plainly whether the claim is right. If it is a common misconception, explain why it seems true and "
        "what is actually the case.",
    ),
    "solve_numerical": Intent(
        "solve",
        "Calculate a number: bandwidth, throughput, delay, MTU, storage or capacity",
        "Calculate a numerical answer from given values: bandwidth, throughput, latency, bandwidth-delay product, "
        "MTU and MSS, storage or capacity. The main task is quantitative. Example: “How long does a 2 GB "
        "transfer take on a 100 Mbps link?”",
        "When you solve it: what is given and asked, the formula, the substitution with units, and the result "
        "with its unit; note any overhead you assumed.",
    ),
    "solve_equation": Intent(
        "solve",
        "Work out addressing: subnets, masks, ranges, wildcards or summary routes",
        "Find unknown addressing values: subnet a block, find a mask, a host range, a wildcard mask, a summary "
        "route or an IPv6 prefix. Example: “Split 10.0.0.0/22 into four equal subnets.”",
        "When you solve it: one step per line saying what was done (the bits borrowed, the block size), then a "
        "table of the results and a check of the first and last addresses.",
    ),
    "step_by_step_solution": Intent(
        "solve",
        "Walk through a task or fix step by step, when they explicitly ask for steps",
        "Solve a provided task while explicitly showing and explaining the sequence of steps. Choose this when "
        "the requested step-by-step method is the main distinguishing requirement; otherwise use the task type. "
        "Example: “Har step explain karke VLAN configure karo.”",
        "When you give the solution, number every step with a short reason, each with its exact command and how "
        "to check it worked.",
    ),
    "textbook_exercise": Intent(
        "solve",
        "Solve a specific numbered lab, exam-guide or course exercise",
        "Answer or solve a specific exercise, lab task or numbered question from a course, an official cert guide "
        "or a lab workbook. The source and exercise context are central. Example: “Solve lab 4.2 from my CCNA "
        "workbook.”",
        "Answer it the way the course or lab expects for that exercise, using the reference passages when they "
        "cover it, and say how to verify the result in the lab.",
    ),
    "homework_help": Intent(
        "solve",
        "Troubleshoot or configure something: an issue, a task or ticket, no narrower ask",
        "Help with a technical task, issue or ticket (troubleshooting, configuration, a change) when the user has "
        "not specified a more precise action such as a calculation, checking an answer, or giving a hint. "
        "Example: “Help me get this site-to-site VPN working.”",
    ),
    "hint_only": Intent(
        "feedback",
        "Give only a hint or next step, not the full answer or configuration",
        "Give a hint, clue or next step without revealing the full answer, solution or configuration. Respect an "
        "explicit request not to give the answer. Example: “Answer mat batana, bas hint do.”",
        "They are practising (a lab or a certification question): give one hint or the next step only. Do not "
        "state the final answer or write the full configuration; if they shared their config or working, point "
        "to where to look without fixing it.",
    ),
    "check_answer": Intent(
        "feedback",
        "Say whether the user's answer, command or config is correct",
        "Determine whether a user's proposed answer, command or configuration is correct. Focus on correctness; "
        "use debug_solution when they specifically want the error in their work explained. Example: “Is "
        "255.255.255.240 right for 14 hosts?”",
        "Check their answer, command or config: say clearly whether it is right; if not, where it first goes "
        "wrong and why, then the correction.",
    ),
    "debug_solution": Intent(
        "feedback",
        "Find where and why the user's config, script or reasoning went wrong",
        "Inspect the user's configuration, script, output or reasoning and identify where and why a mistake "
        "occurred. Example: “Mere ACL mein galti kahan hai?”",
        "Go through their config or work in order, find the first wrong line or step and say why it is wrong, "
        "then continue correctly from there.",
    ),
    "alternative_method": Intent(
        "solve",
        "Show another way: a different command, design, tool or approach",
        "Show a different valid way to do a task when the user asks for another approach: another command, "
        "design, tool or method. Example: “Can I do this with a route-map instead of a prefix-list?”",
        "Show a different method from the one already used (or the usual one), and say when each is simpler, "
        "safer or better supported.",
    ),
    "word_problem": Intent(
        "solve",
        "Turn a described scenario or requirements into a design or configuration",
        "Convert a described scenario or set of requirements into a design, plan or configuration and solve it. "
        "Choose when interpreting the scenario is central. Example: “A branch with 3 VLANs, two ISPs and a "
        "guest Wi-Fi…”",
        "First restate the requirements and constraints (what is given, what must work, what must not break); "
        "then give the design or configuration and how to verify it.",
    ),
    "textbook_lookup": Intent(
        "materials",
        "Find what the docs, an RFC or an exam guide say, or where (section or page)",
        "Find a specific fact, passage, definition, section or page in vendor documentation, an RFC or an exam "
        "guide. The user primarily asks where content is or what the source states. Example: “Which RFC "
        "defines BGP communities?”",
        "Answer from the reference passages (vendor docs, RFCs, guides), close to their wording, and cite them; "
        "if they don't cover it, say so and never invent a section or document name.",
    ),
    "chapter_summary": Intent(
        "materials",
        "Summarise a document, RFC, log, config or a long section",
        "Produce a concise overview of a whole document, RFC, log, configuration or substantial section, "
        "preserving its main points. Example: “Summarise this firewall log for me.”",
        "Summarise it in its own order: the main points as short headed sections, then a three-line recap; for a "
        "log or configuration, what it shows and anything abnormal.",
    ),
    "revision_notes": Intent(
        "materials",
        "Make compact, organised notes for certification revision",
        "Create compact, organised notes for later revision or quick reference, usually combining key concepts "
        "and explanations. Example: “Security+ ke liye one-page notes bana do.”",
        "Write revision notes: short bullet points per topic, key terms in bold, commands and values in code, and "
        "likely exam points at the end.",
    ),
    "key_points": Intent(
        "materials",
        "List only the most important points of a topic, not a full summary",
        "Extract or list the most important facts, ideas or takeaways from a topic or supplied content without "
        "creating a full summary. Example: “Give me the 10 key points of IPsec.”",
        "List only the key points: five to ten short bullets, most important first.",
    ),
    "formula_sheet": Intent(
        "materials",
        "Make a cheat sheet: commands, ports, timers, defaults or formulas",
        "Collect the commands, port numbers, timers, defaults or formulas of a topic or platform, with what each "
        "means. Example: “Make a cheat sheet of OSPF show commands.”",
        "Give a cheat sheet grouped by task or topic: each command or value with what it does, its syntax or "
        "unit, and the platform it applies to.",
    ),
    "flashcards": Intent(
        "materials",
        "Make flashcards: a prompt on one side, the answer on the other",
        "Create study cards with a prompt on one side and an answer or explanation on the other. Example: “Make "
        "flashcards for well-known ports.”",
        "Make flashcards: one term, command or question per card and a one-line answer, 8 to 12 cards unless "
        "they asked for a number.",
    ),
    "mind_map": Intent(
        "materials",
        "Organise a topic as a mind map of main ideas and subtopics",
        "Organise a topic as a hierarchical or connected visual or text structure of main ideas and subtopics. "
        "Example: “Make a mind map of the MITRE ATT&CK tactics.”",
        "Draw it as one mermaid mindmap block: the topic in the centre, its main branches, two or three points on "
        "each.",
    ),
    "translation": Intent(
        "language",
        "Translate given content into another language",
        "Translate provided or clearly identified content from one language into another while preserving "
        "meaning. Example: “Is paragraph ko Hindi mein translate karo.”",
        "Translate exactly what they asked into the language they named, keeping commands, configuration and "
        "technical terms as they are; add nothing else unless asked.",
    ),
    "simplify_text": Intent(
        "language",
        "Rewrite existing text in plainer words, keeping its meaning",
        "Rewrite existing content in plainer words while keeping its original meaning. Choose this when "
        "simplification is requested, not when the user asks to learn a concept from scratch. Example: “Explain "
        "this advisory in plain English for my manager.”",
        "Rewrite it in plainer words and shorter sentences for its reader, keeping every fact and adding nothing new.",
    ),
    "answer_formatting": Intent(
        "language",
        "Change an answer's format, length or tone: a report, ticket, email or table",
        "Change the format, length, tone or structure of an existing or requested answer without changing its "
        "core meaning: a ticket update, change request, incident report, email or table. Example: “Write this "
        "as a change request.”",
        "Format it as they asked (a ticket update, change request, incident report, email or table): key terms "
        "in bold, commands in code, and a length that fits its reader.",
    ),
    "generate_mcq": Intent(
        "tests",
        "Write a set of practice multiple-choice questions (MCQs), not a live quiz",
        "Create multiple-choice certification practice questions with answer options, usually with an answer key "
        "if requested. Choose when generating MCQs is the main task, not running an interactive quiz. Example: "
        "“Give me 20 MCQs on subnetting.”",
        "Give certification practice questions on what they named, with the number and difficulty they asked for.",
        bank=True,
    ),
    "conduct_quiz": Intent(
        "tests",
        "Run an interactive quiz, one question at a time",
        "Run an interactive quiz, typically asking one question at a time and responding to the user's answers. "
        "Example: “Ask me one question at a time.”",
        "Quiz them now on what they named, or on what this chat has covered.",
        bank=True,
    ),
    "mock_test": Intent(
        "tests",
        "Set a full practice exam: timed, with many questions or several domains",
        "Create or administer a longer, exam-like assessment with a defined duration, many questions or several "
        "domains. Example: “Give me a 60-minute CCNA practice exam.”",
        "Make it a full practice exam: about 20 to 30 questions across the domains they named (up to 3), mixed "
        "difficulty, unless they said otherwise.",
        bank=True,
    ),
    "exam_prep": Intent(
        "tests",
        "Help prepare for a certification exam: strategy, coverage or approach",
        "Help prepare for a named or upcoming certification exam through strategy, domain coverage, or an "
        "overall preparation approach. Use study_schedule when the primary request is a plan or timetable. "
        "Example: “CCNP exam mein 10 din bache hain.”",
        "Help them prepare for the certification: what to focus on (its domains and their weighting where "
        "known), how to practise it in labs, and how to pace the exam.",
    ),
    "important_topics": Intent(
        "tests",
        "Name the most important topics or domains to focus on or revise",
        "Identify or prioritise topics, domains or concepts to focus on for revision or an exam. Do not claim a "
        "topic is guaranteed to appear. Example: “Which Security+ domains should I revise first?”",
        "List the most important topics or question types, most important first, each with why it matters; never "
        "claim to know what the exam will contain.",
    ),
    "marking_rubric": Intent(
        "feedback",
        "Explain how an exam or lab is scored: objectives, weighting, passing score",
        "Explain how a certification exam, practical lab or assessment is scored: its objectives, the weighting "
        "and the passing score. Example: “How is the OSCP exam scored?”",
        "Explain how it is scored: the objectives or tasks that carry points, how they are weighted and the "
        "passing score where it is published; say so if the vendor's exact scoring is not public.",
    ),
    "evaluate_written_answer": Intent(
        "feedback",
        "Assess the user's written answer, design or report and give feedback",
        "Assess a user's written answer, design document or report against a prompt, rubric or expected answer; "
        "provide feedback or a score if requested. Example: “Review my pentest report summary.”",
        "Review it as an exam grader or senior reviewer would: what is right, what is missing or wrong, how to "
        "improve it. Give a score only if the scale is known; never invent a marking scheme.",
    ),
    "difficulty_adjustment": Intent(
        "tests",
        "Make questions, practice or explanations harder or easier",
        "Change the difficulty of existing or future questions, practice or explanations. Example: “Ab thode "
        "difficult questions do.”",
        "Change the level as they asked (harder or easier) and keep the same topic; for a test, set a new one at "
        "that difficulty.",
        bank=True,
    ),
    "performance_analysis": Intent(
        "planning",
        "Analyse the user's own practice results or progress: strengths, weaknesses",
        "Analyse completed quiz or practice test results or past progress to identify strengths, weaknesses and "
        "areas to improve. Example: “Which topics am I weak in?”",
        'Answer from the app\'s records in these instructions (the "Practice so far" section: its activity and '
        "practice record lines; and this chat's practice tests) and quote them exactly. If there is no record of "
        "what they ask about, say so.",
        retrieval=False,
    ),
    "study_schedule": Intent(
        "planning",
        "Make a certification study or lab plan over a stated period",
        "Create a study plan or timetable for a certification or lab practice, with sessions and time allocation "
        "over a stated period. Example: “Make a 30-day plan for my CCNA.”",
        "Make a realistic study plan as a table (days or weeks, domain or topic, time, labs, revision and "
        "practice tests), using their certification goal, focus areas and practice record where given.",
        retrieval=False,
    ),
    "diagram_explanation": Intent(
        "visual_lab",
        "Explain a network diagram or topology: what it shows and how parts connect",
        "Explain what a supplied or clearly identified diagram, topology or flow represents and how its parts "
        "work together. Example: “Explain this three-tier campus topology.”",
        "Explain the diagram part by part and how the parts work together, from the reference passages or their "
        "description. You cannot see images: if it is unclear which diagram they mean, ask.",
    ),
    "diagram_labeling": Intent(
        "visual_lab",
        "Name or label the parts of a diagram, frame, packet header or topology",
        "Name, identify or place labels on the parts of a diagram, frame, packet header or topology. Example: "
        "“Label the fields of an IPv4 header.”",
        "Give the labels as a numbered list (label, then what it is) for the diagram or header they mean. You "
        "cannot see images: if it is unclear which one, ask.",
    ),
    "graph_interpretation": Intent(
        "visual_lab",
        "Read a graph, chart or monitoring data: values, trends, axes, conclusions",
        "Read or explain values, trends, axes, relationships or conclusions from a graph, chart or monitoring "
        "data. Example: “What does this interface utilisation graph show?”",
        "Read it from what they gave (data, axes, the attached file's text): each axis or field, the trend, key "
        "values and what it means; never invent values.",
    ),
    "image_question_solving": Intent(
        "visual_lab",
        "Answer a question in an uploaded screenshot or photo (not a diagram or graph)",
        "Answer a question contained in an uploaded or supplied screenshot or photo when it is not more "
        "specifically a diagram explanation, diagram labelling or graph interpretation task. Example: “Solve "
        "question 4 in this screenshot.”",
        "The question is in their attached file: work from its text, and restate the question in one line first. "
        "If the text is missing or unreadable, say so and ask them to paste it.",
    ),
    "experiment_procedure": Intent(
        "visual_lab",
        "Explain how to build or run a lab: devices, topology, steps",
        "Explain the devices, software, topology or steps for building or running a lab, such as a homelab, a "
        "simulator lab or a test bed. Example: “How do I build a lab to practise BGP in GNS3?”",
        "Give the lab as goal, devices and software, topology, numbered steps with commands, the expected results "
        "and how to verify them.",
    ),
    "observation_inference": Intent(
        "visual_lab",
        "Explain what lab results, test output or observations show",
        "Interpret lab results, test output or observations and explain what conclusion they support. Example: "
        "“Why did the ping succeed but the traceroute stop at hop 3?”",
        "For each observation or output line, state what it shows and why it follows; keep observations and "
        "inferences separate, and say what it does not prove.",
    ),
    "lab_safety": Intent(
        "visual_lab",
        "Lab and change safety: risks, isolation, backups, rollback, authorisation",
        "Explain the risks, precautions and safe practice for a lab, a production change or a security test: "
        "isolation, backups, rollback and authorisation. Example: “What should I check before running this scan "
        "on our network?”",
        "Give the precautions as a short list, most important first (risks in a warning callout): isolate the lab "
        "from production, back up configs, plan the rollback, and stay within authorised scope.",
    ),
    "project_assignment": Intent(
        "visual_lab",
        "Help plan or build a project: a homelab, a design, a tool or a portfolio piece",
        "Help plan or prepare a project deliverable, such as a homelab, a network design, a security tool, a "
        "write-up or a portfolio piece. Example: “Help me plan a homelab project for my CV.”",
        "Help them plan and do it themselves: an outline, steps, the tools and equipment, and how to document or "
        "present it; write sections or full configs only when they ask.",
    ),
}

GROUP_MEMBERS: dict[str, tuple[str, ...]] = {
    group: tuple(name for name, intent in INTENTS.items() if intent.group == group)
    for group in GROUPS
    if group != ABSTAIN
}
BANK_INTENTS = frozenset(name for name, intent in INTENTS.items() if intent.bank)
