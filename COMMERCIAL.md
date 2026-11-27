# Licensing

arp273 is offered under two licences. Which one you need depends on what you do with it.

|  | Open licence | Commercial licence |
|---|---|---|
| Licence | CERN-OHL-S-2.0, the text is in [`LICENSE`](LICENSE) | An individual written agreement with the copyright holder |
| Cost | Free | Agreed per case, royalties included |
| Conditions | The conditions of CERN-OHL-S-2.0, in particular those that apply when you convey the design or a product made from it | Agreed per case |
| You need it for | Everything below that is marked free | A use that cannot meet the conditions of CERN-OHL-S-2.0 (see below) |

## Do I need a commercial licence?

| You want to | Licence |
|---|---|
| Read, study, simulate, test the design | Open, free |
| Run it on an FPGA for yourself, in a lab or in a course | Open, free |
| Evaluate it inside your company, with no product and no delivery of the design to anyone | Open, free |
| Change it and publish your changes and designs under CERN-OHL-S-2.0 | Open, free |
| Use it in a product and give the complete source of that product to everyone who receives it, as CERN-OHL-S-2.0 requires | Open, free |
| Use it, or anything derived from it, in a product or a chip whose source you will not publish | **Commercial** |
| Embed it in a larger design that you keep closed | **Commercial** |
| Keep your changes to it closed while you deliver the result to others | **Commercial** |

If your case is not in the table, or you are not sure, ask before you build on it. The licence text decides,
not this table.

## How to ask for a commercial licence

Write to the copyright holder, Denys Kachanov, through the contact on the GitHub profile of the repository
owner. Please do not use an issue or a pull request for this. It helps to say:

- who you are (company, country), and what the product is;
- which parts you want: the RTL, the documentation, the tests and scripts, or all of it;
- the expected volume and time frame;
- whether you need changes or support.

A commercial licence is an agreement between you and the copyright holder. It is not published, and none of
the terms in this repository is an offer; the copyright holder may accept a request or decline it.

## What a commercial licence does not cover

- Material of other authors that is in or comes with the repository (`THIRD_PARTY.md`): it stays under its
  own licence, and the copyright holder cannot license it.
- Commits made before the repository changed to CERN-OHL-S-2.0 (see ADR 001 and the git history) were
  published under Apache-2.0 and stay available under it. This file is about the current licensing.
- The licence headers in the source files (`SPDX-License-Identifier: CERN-OHL-S-2.0`) do not change when a
  commercial licence is granted: it is a separate agreement and does not relicense the public repository.
