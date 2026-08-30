// Docusaurus sidebar config — drop into your Docusaurus project alongside docusaurus.config.js.
// Mirrors mkdocs.yml's nav so the two stay aligned.

module.exports = {
  docs: [
    "index",
    "installation",
    "quickstart",
    "concepts",
    "benchmark",
    {
      type: "category",
      label: "Usage Guide",
      collapsed: false,
      items: [
        "usage/basic",
        "usage/advanced",
        "usage/configuration",
        "usage/customization",
      ],
    },
    {
      type: "category",
      label: "Scenarios",
      collapsed: false,
      link: { type: "doc", id: "scenarios/scenarios-index" },
      items: [
        "scenarios/simple-suite",
        "scenarios/large-suites",
        "scenarios/edge-cases",
        "scenarios/ci-cd",
        "scenarios/openai-adapter",
        "scenarios/performance",
        "scenarios/debugging",
        "scenarios/failure-handling",
      ],
    },
    {
      type: "category",
      label: "Reference",
      collapsed: false,
      items: [
        "api/python-api",
        "api/cli",
        "api/graders",
        "api/adapters",
      ],
    },
    {
      type: "category",
      label: "Guides",
      collapsed: false,
      items: ["comparison", "architecture", "best-practices", "troubleshooting", "faq"],
    },
    {
      type: "category",
      label: "Project",
      collapsed: false,
      items: ["contributing", "roadmap"],
    },
  ],
};
