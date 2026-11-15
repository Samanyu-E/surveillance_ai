WhatsApp alerts in Surveillance.ai

Open the live dashboard, choose **Camera setup**, and scroll to **WhatsApp notifications**. The regular Windows and macOS launchers support these settings; no extra command-line switch is needed.

1. Create or open your Twilio account and follow its [WhatsApp quickstart](https://www.twilio.com/docs/whatsapp/quickstart). For a trial, connect your phone through **Try out WhatsApp**. If using the legacy Sandbox, send its `join …` code from your WhatsApp to the Sandbox number shown in your console. Use the sender number shown for your own testing environment.
2. Enter the Twilio **Account SID**, **Auth Token**, **WhatsApp sender**, and **your WhatsApp number** in Camera setup. Use `+` and the country code for both numbers. Keep the Auth Token in the local app; do not post it in chat or commit it to source control.
3. For an initial free-form text test, send a WhatsApp message from your recipient phone to the Twilio number. This opens WhatsApp's 24-hour reply window.
4. Check **Enable WhatsApp alerts**, then click **Send test notification**. This saves your settings and sends one test to the saved recipient. “Accepted by Twilio” means the provider queued/accepted the request; confirm arrival on your phone. If it does not arrive, inspect the message in Twilio Messaging logs.
5. Start monitoring. New **possible walk-out** and **counter-wait** alerts send a text with the guest ID, alert message, and timestamp. Existing history does not trigger messages. Camera images and clips stay local. The computer must remain running and connected to the internet.

For alerts outside the 24-hour reply window, use a registered WhatsApp sender and an approved WhatsApp template. This app supports a template with one variable containing the alert text, for example `Surveillance.ai alert: {{1}}`. Paste the template's **HX Content SID** into the optional template field. Other templates with additional required variables will need an adapter. Approval is handled by WhatsApp/Twilio; entering an HX SID does not create or approve a template. Sandbox testing is not a production setup.

Official instructions: [WhatsApp Sandbox](https://www.twilio.com/docs/whatsapp/sandbox), [approved notification templates](https://www.twilio.com/docs/whatsapp/tutorial/send-whatsapp-notification-messages-templates). Twilio/WhatsApp usage may incur charges; check your account's pricing before leaving alerts enabled.

Settings are saved in the project/config directory's ignored `.env` file. Leaving the Auth Token field blank retains the saved token. Unchecking **Enable WhatsApp alerts** and saving stops future automatic sends without stopping the camera. Already submitted messages cannot be recalled. Each new alert reloads settings, so changing the recipient, token, or template does not require restarting monitoring. The separate test button can send a manual test while automatic alerts are disabled.

The original environment-variable setup remains supported:

```dotenv
WHATSAPP_ENABLED=true
TWILIO_ACCOUNT_SID=AC_your_account_sid
TWILIO_AUTH_TOKEN=your_auth_token
TWILIO_WHATSAPP_FROM=+your_sender_with_country_code
TWILIO_WHATSAPP_TO=+your_recipient_with_country_code
TWILIO_WHATSAPP_CONTENT_SID=
```

Replace the placeholders with values from your account. The Account SID must be `AC` plus 32 hexadecimal characters; template SIDs are `HX` plus 32 hexadecimal characters. Saved `.env` values take precedence over inherited environment variables. `--whatsapp` still enables delivery when `WHATSAPP_ENABLED` has not been explicitly set.
