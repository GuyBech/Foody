import { anthropic, model } from "./client";

/**
 * Ingredient resolution is the core primitive for Smart Inventory Deduction.
 * Given a freeform meal description ("chicken stir-fry for 2"), produce a
 * normalized list of ingredient deductions to apply to the household pantry.
 *
 * Keep prompts short; structured output is enforced via JSON tool schema.
 */
export type IngredientDeduction = {
  name: string;
  quantity: number;
  unit: string;
  confidence: number; // 0..1
};

const SYSTEM = `You translate meals into pantry deductions. Return concise,
normalized ingredient names (singular, lowercase) and realistic quantities for
the serving count. Never invent units; pick from g, ml, pcs, tbsp, tsp, cup.`;

export async function inferDeductions(input: {
  meal: string;
  servings: number;
  pantry: Array<{ name: string; unit: string; quantity: number }>;
}): Promise<IngredientDeduction[]> {
  const res = await anthropic().messages.create({
    model: model(),
    max_tokens: 1024,
    system: SYSTEM,
    messages: [
      {
        role: "user",
        content: [
          {
            type: "text",
            text: `Meal: ${input.meal}\nServings: ${input.servings}\nPantry (hints):\n${input.pantry
              .map((p) => `- ${p.name} (${p.quantity} ${p.unit})`)
              .join("\n")}\n\nReturn a JSON array of {name, quantity, unit, confidence}.`,
          },
        ],
      },
    ],
  });

  const text = res.content
    .map((c) => (c.type === "text" ? c.text : ""))
    .join("\n");
  const jsonStart = text.indexOf("[");
  const jsonEnd = text.lastIndexOf("]");
  if (jsonStart === -1 || jsonEnd === -1) return [];
  return JSON.parse(text.slice(jsonStart, jsonEnd + 1)) as IngredientDeduction[];
}
