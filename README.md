Clausegraph — Contract Review Agent

Clausegraph reviews a contract, flags risky or missing clauses, explains why each one matters, suggests a rewording, and pauses so a human can approve or edit those suggestions before a final report is produced. It is built as a LangGraph pipeline behind an MCP server, with a Streamlit app and a command line client on top.

This is not legal advice. It is a first pass review tool meant to make a human reviewer's job faster and more consistent. Always have flagged, high stakes contracts reviewed by a qualified lawyer.


Features

Clause detection and risk flagging. The tool scans a contract against a fixed set of clause types and flags each one as high, medium, or low risk, along with a reason.

LLM grounded explanations. For every high or medium risk flag, an LLM (through OpenRouter) writes a plain language explanation and a suggested rewording. These are grounded in a curated knowledge base of standard clause language and known red flags, rather than relying only on the model's general training.

Jurisdiction grounding. You can pick a jurisdiction, currently Nigeria, with US and EU support still being filled in, to ground explanations in jurisdiction specific reference notes.

Human in the loop review gate. The pipeline genuinely pauses, using LangGraph's interrupt function and backed by a checkpointer, after generating explanations. It waits for a human to edit and approve each flagged clause before the final report is assembled. This is a real pause and resume of the pipeline itself, not something faked in the interface.

Highlighted contract view. The full contract text is shown with each flagged clause highlighted according to its risk level, so you can see the risky language in its original context.

Version comparison. You can upload an older and a newer version of a contract to see which clauses were added, removed, or reworded, and which risks were introduced or resolved. This comparison flow does not include the human review gate.

Exports. You can download the report as plain text or JSON, and download an updated contract or a change summary as a Word document or PDF, reflecting whichever clause rewordings were approved.


Project structure

contract_review_agent is the main folder.

Inside it, mcp_server holds app.py, which is the MCP server exposing the risk explanation and negotiation memo tools, and knowledge_base.py, which holds the standard clause language, red flags, and jurisdiction notes.

Also inside it, pipeline holds graph.py, the LangGraph pipeline that handles detection, flagging, explanation, and the review gate; parsing.py, which extracts text and page numbers from PDF, Word, and text files; risk_rules.py, which does rule based clause detection and risk scoring; and document_builder.py, which builds the updated contract and change summary files in Word and PDF format.

At the top level there is also app_streamlit.py, the Streamlit front end; client.py, the command line client for reviewing a single contract or comparing two; requirements.txt; and sample_contract.txt.


Before you start

You need Python 3.10 or newer, and an OpenRouter API key, which is what powers the LLM generated explanations, rewordings, and executive summary.


Setup

First, clone the repository and create a virtual environment.

git clone your repo url
cd contract_review_agent
python -m venv venv

Then activate it. On Windows in PowerShell, run venv\Scripts\Activate.ps1. On macOS or Linux, run source venv/bin/activate.

Next, install the dependencies with pip install -r requirements.txt.

Finally, set your API key. Create a file named .env in the project root containing a single line, OPENROUTER_API_KEY equals your key. Without this, LLM calls will silently fall back to generic template text instead of real explanations, so if every explanation looks the same or oddly generic, check this first.


Running it, option one, the Streamlit app

This is the recommended way to use it. Run streamlit run app_streamlit.py. This starts the MCP server automatically in the background the first time you run it, so you do not need to start it separately.

In the browser tab that opens, upload a PDF, Word, or text contract. Optionally pick a jurisdiction to ground the explanations in local reference notes. Click Run review. The pipeline detects clauses, flags risks, and generates explanations and rewordings for every high or medium flag, then pauses. Edit and approve each flagged clause's rewording in the review form that appears. Click Submit review and finalize report to resume the pipeline and produce the final report, with approval marks on each clause. From there you can download the report as text or JSON, or download an updated contract or change summary as a Word document or PDF.

Further down the same page there is a section for comparing two contract versions, where you can upload an older and a newer version to see what changed between them.


Running it, option two, the command line client

Start the MCP server in one terminal window with cd mcp_server followed by python app.py. You should see it print LLM backend, OpenRouter, openai slash gpt-4o-mini. If it instead says none, using rule based fallbacks, your .env file is not being read correctly.

In a second terminal window, run a review with python client.py followed by the path to your contract file. To compare two versions instead, run python client.py followed by the path to the older file and then the path to the newer file.

The command line client automatically approves every clause, since it does not have a way to show the review gate interactively, and it prints the report straight to the terminal, including page citations where they are available.

Note that the client does not start the server for you. If the server is not running, or if it is listening somewhere other than where the client expects, the client will fail right away with a clear connection error rather than quietly starting a server on its own.


How the review gate works

Only high and medium risk flags are paused on for human review. Low risk flags are automatically approved, since they are never shown to the reviewer in the first place.

The paused state is held in memory by LangGraph's checkpointer. If you restart the app while a review is paused partway through, that paused state is lost, and you will need to run the review again from the start. This is fine for a normal single sitting review, but if you need paused reviews to survive an app restart, you would need to swap the in memory checkpointer for a persistent one, such as one backed by SQLite, inside graph.py.

If a contract has nothing worth flagging, the pipeline returns the finished report right away, with no pause at all.


Extending it

To add a new jurisdiction, open knowledge_base.py, add a new entry under each clause type's jurisdiction notes, and then add the jurisdiction's name to the list of options in app_streamlit.py.

To add a new clause type or risk rule, extend risk_rules.py and add a matching entry in knowledge_base.py.

To require full approval before downloads are allowed, note that right now the final report and the exported files are available regardless of whether every clause was approved, they simply show an approved or not approved mark. Blocking downloads until everything is approved would be a small addition to the function that renders the final report.


A final note

Clausegraph is reference material and a review workflow meant to make contract review faster, more consistent, and easier to audit. It is not a substitute for legal advice. Always have flagged, high stakes contracts reviewed by a qualified lawyer before relying on them.
