# AI Usage Declaration

This file documents the use of AI tools in the development of this project.

## Tools Used

- **Claude (Anthropic)** — Used for design consultation and implementation assistance.
- **Codex** — Used for reasoning about code correctness and identifying edge cases.
- **Gemini CLI** — Used for code review and finding incorrect logic.

## Usage Details

AI assistance was used for:
- Designing the overall architecture and layer interface contracts (Result objects)
- Implementing the B+ tree insert/split logic
- Implementing the buffer manager LRU/MRU replacement using OrderedDict
- Writing the slotted page encode/decode routines
- Reasoning about whether code would work correctly during bug fixes and new feature implementation
- Identifying possible edge cases in index maintenance (hash index and B+ tree consistency after insertions and deletions)
- Reviewing code logic to catch incorrect behavior that manual inspection might miss

All code was reviewed, tested, and understood by the team before submission.
