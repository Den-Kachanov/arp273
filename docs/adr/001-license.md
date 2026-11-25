# ADR 001: CERN-OHL-S-2.0 license and a public repository from the first commit

Status: draft, pending author review
Changed: the initial choice of Apache-2.0 was replaced with CERN-OHL-S-2.0

## Context

The project is a chip-design portfolio. Engineers and recruiters are expected to read and assess it, so the repository is open and its legal terms are clear. All RTL, tests and scripts are written from scratch, without borrowed code. Without a `LICENSE` file the code is not open source, even in a public repository.

The author wants the project to be protected: if someone uses it commercially (in particular to manufacture chips), the author should have leverage to obtain a share. A permissive license (Apache-2.0) does not provide this: it allows a closed derivative and requires no payment.

A separate question was when to open the repository: immediately or after completion. The author chose public from the first commit.

## Options

| Option | Pros | Cons |
|---|---|---|
| Apache-2.0 | Allows commercial use, explicit patent license, familiar to employers | Does not require changes to be shared back; gives no leverage for payment; not designed for chip manufacturing |
| MIT | Simplest | Same as above, plus no patent grant |
| CERN-OHL-P-2.0, Solderpad 2.1 | Permissive, adapted to hardware | Give no leverage for payment |
| **CERN-OHL-S-2.0** | Designed for hardware; strong copyleft: derived designs and products based on them must be distributed under the same terms; remains genuine open source; the author, as the sole copyright holder, can offer a commercial license | Less familiar; copyleft for hardware is less tested legally; some companies avoid such code |
| CERN-OHL-W-2.0 | Weak copyleft, easier for companies | Weaker leverage |
| GPL-3.0 / AGPL-3.0 | Copyleft for code | Aimed at software, not hardware |
| CC BY-NC, PolyForm Noncommercial | Strongest control over commercial use | Not open source; "non-commercial" is vaguely defined |
| Private repository, opened at the end | Less risk of accidental leaks | The work history is lost as evidence; CI limits; risk of a large merge before publication |

Splitting licenses by directory (RTL under CERN-OHL-S, the rest under Apache-2.0) was rejected: CERN-OHL-S defines "Source" as the material needed to make or test a product, so test scripts are covered anyway, and a split would complicate SPDX tags, CI checks and dual licensing.

## Decision

1. **CERN-OHL-S-2.0** for the whole repository. The text is taken from the official SPDX list.
2. The repository is **public from the first commit**.
3. `LICENSE`, and the copyright line `Copyright (c) 2026 Denys Kachanov` in `NOTICE`; the SPDX header `CERN-OHL-S-2.0` in every source file; a check in CI.
4. The author remains the sole copyright holder: external pull requests are accepted only after an agreement to assign rights (CLA), to keep a commercial license possible.
5. Third-party material (`riscv-tests` as a submodule, data, models) is listed in `THIRD_PARTY.md` with its license; compatibility with the copyleft is checked before anything is added.
6. Secret hygiene: `.gitignore` from the first commit, `gitleaks` in pre-commit and CI, a noreply address in git, secret scanning and push protection on GitHub.

## Consequences

- Copyleft creates leverage, not guaranteed income. A company that does not want to disclose its changes can buy a commercial license; many companies will simply not use such code. Enforcement costs money and needs legal help.
- The first commits were made under Apache-2.0 (before the change) and remain available under those terms to those who received them. New versions are under CERN-OHL-S-2.0.
- The license is irrevocable for those who have already received the code. Patents, if wanted, must be filed before the corresponding idea is published.
- The commit history is public immediately and permanently. A secret in a commit is considered leaked even if it is removed later; the remedy is to revoke the secret and, if needed, create a new repository.
- The project is not assessed as part of the author's studies, so a claim on the rights by the university (UCU) is unlikely. Before the first public commit, confirm this with UCU or the lecturer in one email and keep the reply.
- The terms of future employers are unknown. The repository is created before any contract; when signing an internship, list it as prior work and read the IP clauses.
- The name "RISC-V" is not part of the repository name; the documentation says "implements RV64I".
- Any real commercialisation, especially chip manufacturing, needs legal advice first. This draft is not legal advice.
