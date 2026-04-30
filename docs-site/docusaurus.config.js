// @ts-check
// Docusaurus configuration for agentprdiff. Mirrors the structure documented
// in mkdocs.yml so the two static-site generators stay aligned.

const { themes } = require("prism-react-renderer");

/** @type {import('@docusaurus/types').Config} */
const config = {
  title: "agentprdiff",
  tagline: "Snapshot tests that catch behavioral regressions in your LLM agents.",
  url: "https://vnageshwaran-de.github.io",
  baseUrl: "/agentprdiff/",
  favicon: "img/favicon.ico",

  organizationName: "vnageshwaran-de",
  projectName: "agentprdiff",
  trailingSlash: false,

  onBrokenLinks: "throw",
  onBrokenMarkdownLinks: "warn",

  i18n: {
    defaultLocale: "en",
    locales: ["en"],
  },

  markdown: {
    mermaid: true,
  },
  themes: ["@docusaurus/theme-mermaid"],

  presets: [
    [
      "classic",
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          path: "docs",
          routeBasePath: "/",
          sidebarPath: require.resolve("./sidebars.js"),
          editUrl:
            "https://github.com/vnageshwaran-de/agentprdiff/edit/main/docs-site/",
          showLastUpdateTime: true,
        },
        blog: false,
        theme: {
          customCss: require.resolve("./src/css/custom.css"),
        },
      }),
    ],
  ],

  themeConfig:
    /** @type {import('@docusaurus/preset-classic').ThemeConfig} */
    ({
      colorMode: {
        defaultMode: "light",
        respectPrefersColorScheme: true,
      },
      navbar: {
        title: "agentprdiff",
        logo: {
          alt: "agentprdiff",
          src: "img/logo.svg",
        },
        items: [
          { to: "/quickstart", label: "Quickstart", position: "left" },
          { to: "/concepts", label: "Concepts", position: "left" },
          { to: "/api/python-api", label: "API", position: "left" },
          { to: "/api/cli", label: "CLI", position: "left" },
          {
            href: "https://github.com/vnageshwaran-de/agentprdiff",
            label: "GitHub",
            position: "right",
          },
          {
            href: "https://pypi.org/project/agentprdiff/",
            label: "PyPI",
            position: "right",
          },
        ],
      },
      footer: {
        style: "dark",
        links: [
          {
            title: "Docs",
            items: [
              { label: "Quickstart", to: "/quickstart" },
              { label: "Core Concepts", to: "/concepts" },
              { label: "CLI Reference", to: "/api/cli" },
            ],
          },
          {
            title: "Community",
            items: [
              {
                label: "GitHub Issues",
                href: "https://github.com/vnageshwaran-de/agentprdiff/issues",
              },
            ],
          },
          {
            title: "More",
            items: [
              {
                label: "PyPI",
                href: "https://pypi.org/project/agentprdiff/",
              },
              {
                label: "Changelog",
                href: "https://github.com/vnageshwaran-de/agentprdiff/blob/main/CHANGELOG.md",
              },
            ],
          },
        ],
        copyright: `Copyright © ${new Date().getFullYear()} Vinoth Nageshwaran. MIT License.`,
      },
      prism: {
        theme: themes.github,
        darkTheme: themes.dracula,
        additionalLanguages: ["bash", "python", "yaml", "json", "diff"],
      },
      algolia: undefined, // wire up DocSearch when ready
    }),
};

module.exports = config;
