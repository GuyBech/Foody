import Anthropic from "@anthropic-ai/sdk";
import { serverEnv } from "@/lib/env";

let _client: Anthropic | null = null;

export function anthropic() {
  if (!_client) {
    _client = new Anthropic({ apiKey: serverEnv().ANTHROPIC_API_KEY });
  }
  return _client;
}

export function model() {
  return serverEnv().ANTHROPIC_MODEL;
}
