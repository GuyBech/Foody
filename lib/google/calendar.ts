import { google, type calendar_v3 } from "googleapis";
import { OAuth2Client } from "google-auth-library";
import { serverEnv } from "@/lib/env";

export const GOOGLE_SCOPES = [
  "https://www.googleapis.com/auth/calendar.readonly",
  "https://www.googleapis.com/auth/userinfo.email",
  "openid",
];

export function oauthClient(): OAuth2Client {
  const env = serverEnv();
  return new google.auth.OAuth2(
    env.GOOGLE_CLIENT_ID,
    env.GOOGLE_CLIENT_SECRET,
    env.GOOGLE_REDIRECT_URI,
  );
}

export function getAuthUrl(state: string) {
  return oauthClient().generateAuthUrl({
    access_type: "offline",
    prompt: "consent",
    scope: GOOGLE_SCOPES,
    state,
  });
}

export async function exchangeCode(code: string) {
  const { tokens } = await oauthClient().getToken(code);
  return tokens;
}

export function calendarFor(accessToken: string, refreshToken?: string) {
  const auth = oauthClient();
  auth.setCredentials({ access_token: accessToken, refresh_token: refreshToken });
  return google.calendar({ version: "v3", auth });
}

/** Fetches events in a window. Caller is responsible for persisting to `calendar_events`. */
export async function listEvents(params: {
  accessToken: string;
  refreshToken?: string;
  calendarId?: string;
  timeMin: Date;
  timeMax: Date;
}): Promise<calendar_v3.Schema$Event[]> {
  const cal = calendarFor(params.accessToken, params.refreshToken);
  const { data } = await cal.events.list({
    calendarId: params.calendarId ?? "primary",
    timeMin: params.timeMin.toISOString(),
    timeMax: params.timeMax.toISOString(),
    singleEvents: true,
    orderBy: "startTime",
    maxResults: 250,
  });
  return data.items ?? [];
}
