"""AgentProof production app package. Empty on purpose — NO sys.path shim.
Root modules (framework, teamspec, agent, …) are imported via PYTHONPATH at launch
(see scripts/serve.sh), so this package does not mutate sys.path. This keeps IDE
navigation and type checkers working."""
