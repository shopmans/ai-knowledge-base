import type { Plugin } from "@opencode-ai/plugin";

const TARGET_TOOLS = new Set(["write", "edit"]);
const TARGET_PATTERN = /knowledge\/articles\/[^/]+\.json$/;

const plugin: Plugin = async (input) => {
  const $ = input.$;

  return {
    "tool.execute.after": async (input) => {
      try {
        const toolName = input.tool;
        if (!TARGET_TOOLS.has(toolName)) return;

        const filePath: string | undefined =
          input.args?.file_path ?? input.args?.filePath;
        if (!filePath || !TARGET_PATTERN.test(filePath)) return;

        await $`python3 hooks/validate_json.py ${filePath}`.nothrow();
      } catch {
        // swallow errors to avoid blocking the Agent
      }
    },
  };
};

export default plugin;
