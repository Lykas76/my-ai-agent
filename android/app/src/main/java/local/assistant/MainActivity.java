package local.assistant;

import android.Manifest;
import android.app.Activity;
import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;
import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.view.WindowManager;
import android.view.Gravity;
import android.graphics.Color;
import android.graphics.Typeface;
import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.text.style.StyleSpan;
import android.widget.*;
import android.text.InputType;
import org.json.*;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private LinearLayout root, content;
    private ApiClient api;
    private String sessionId;
    private volatile int generation;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private VoiceProvider voice;
    private boolean speakNextReply;
    interface Job { JSONObject run() throws Exception; }
    interface Result { void accept(JSONObject value) throws Exception; }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);

        voice = new VoiceProvider.AndroidSpeech(this);

        if (checkSelfPermission(
                Manifest.permission.RECORD_AUDIO
            ) != PackageManager.PERMISSION_GRANTED) {

            requestPermissions(
                new String[]{Manifest.permission.RECORD_AUDIO},
                1002
            );
        }

        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS)
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                new String[]{Manifest.permission.POST_NOTIFICATIONS},
                1001
            );
        }

        verifyEmbeddedPython();
        syncLocalReminders();
        toolsPage();
    }
    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private CharSequence markdown(String value) {
        SpannableStringBuilder out = new SpannableStringBuilder();
        int pos = 0;

        while (pos < value.length()) {
            int start = value.indexOf("**", pos);

            if (start < 0) {
                out.append(value.substring(pos));
                break;
            }

            out.append(value.substring(pos, start));

            int end = value.indexOf("**", start + 2);

            if (end < 0) {
                out.append(value.substring(start));
                break;
            }

            int boldStart = out.length();
            out.append(value.substring(start + 2, end));

            out.setSpan(
                new StyleSpan(Typeface.BOLD),
                boldStart,
                out.length(),
                Spanned.SPAN_EXCLUSIVE_EXCLUSIVE
            );

            pos = end + 2;
        }

        return out;
    }

    private void verifyEmbeddedPython() {
        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(this));
            }

            Python python = Python.getInstance();
            PyObject module = python.getModule("mobile_bridge");
            String result = module.callAttr("ping").toString();

            String sqliteResult = module.callAttr(
                "sqlite_test",
                getFilesDir().getAbsolutePath()
            ).toString();

            Toast.makeText(
                this,
                result + " | " + sqliteResult,
                Toast.LENGTH_LONG
            ).show();

        } catch (Exception e) {
            Toast.makeText(
                this,
                "Embedded Python error: " + e.getClass().getSimpleName(),
                Toast.LENGTH_LONG
            ).show();
        }
    }

    private void page(String title) {
        generation++;

        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(16), dp(18), dp(16), dp(12));
        root.setBackgroundColor(getColor(R.color.bg));

        setContentView(root);

        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(4), dp(4), dp(4), dp(12));

        TextView logo = new TextView(this);
        logo.setText("\u2726");
        logo.setTextSize(28);
        logo.setTextColor(getColor(R.color.primary));
        header.addView(logo);

        TextView heading = new TextView(this);
        heading.setText(title);
        heading.setTextSize(21);
        heading.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        heading.setTextColor(getColor(R.color.text_primary));
        heading.setPadding(dp(10), 0, 0, 0);

        header.addView(
            heading,
            new LinearLayout.LayoutParams(0, -2, 1)
        );

        TextView online = new TextView(this);
        online.setText("\u25CF");
        online.setTextColor(getColor(R.color.success));
        online.setTextSize(12);
        header.addView(online);

        root.addView(header);

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);

        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setPadding(0, dp(6), 0, dp(16));

        scroll.addView(content);

        root.addView(
            scroll,
            new LinearLayout.LayoutParams(-1, 0, 1)
        );
    }

    private void label(LinearLayout parent,String text) {
        TextView view = new TextView(this);
        view.setText(markdown(text));
        view.setTextSize(16);
        view.setTextColor(getColor(R.color.text_primary));
        view.setLineSpacing(0, 1.08f);
        view.setPadding(dp(4), dp(10), dp(4), dp(10));
        parent.addView(view);
    }

    private void messageBubble(String text, boolean user) {
        TextView view = new TextView(this);
        view.setText(markdown(text));
        view.setTextSize(16);
        view.setTextColor(Color.WHITE);
        view.setPadding(dp(16), dp(12), dp(16), dp(12));

        view.setBackgroundResource(
            user ? R.drawable.bg_message_user
                 : R.drawable.bg_message_assistant
        );

        LinearLayout.LayoutParams params =
            new LinearLayout.LayoutParams(-2, -2);

        params.gravity = user ? Gravity.END : Gravity.START;

        if (user) {
            params.setMargins(dp(48), dp(5), 0, dp(5));
        } else {
            params.setMargins(0, dp(5), dp(48), dp(5));
        }

        content.addView(view, params);
    }

    private EditText input(String hint) {
        EditText view = new EditText(this);
        view.setHint(hint);
        view.setSaveEnabled(false);
        view.setTextColor(getColor(R.color.text_primary));
        view.setHintTextColor(getColor(R.color.text_muted));
        view.setTextSize(16);
        view.setPadding(dp(16), dp(12), dp(16), dp(12));
        view.setBackgroundResource(R.drawable.bg_input);

        LinearLayout.LayoutParams params =
            new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, dp(8), 0, dp(8));

        content.addView(view, params);
        return view;
    }

    private void button(LinearLayout parent,String title,Runnable action) {
        Button view = new Button(this);
        view.setText(title);
        view.setTextColor(Color.WHITE);
        view.setTextSize(14);
        view.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        view.setAllCaps(false);
        view.setBackgroundResource(R.drawable.bg_primary_button);
        view.setOnClickListener(v -> action.run());

        LinearLayout.LayoutParams params =
            new LinearLayout.LayoutParams(-1, -2);
        params.setMargins(0, dp(5), 0, dp(5));

        parent.addView(view, params);
    }

    private void request(Job job,Result result) {
        final int expectedGeneration = generation;
        worker.submit(() -> {
            if (expectedGeneration != generation) return;
            try { JSONObject value = job.run(); runOnUiThread(() -> {
                if (expectedGeneration != generation || isFinishing() || isDestroyed()) return;
                try { result.accept(value); } catch (Exception e) { error(); }
            }); } catch (Exception e) { runOnUiThread(() -> { if(expectedGeneration == generation) error(); }); }
        });
    }
    private void error() {
        if (!isFinishing() && !isDestroyed()) Toast.makeText(this,
            "Запрос не завершён. Проверьте состояние действия перед повтором.",Toast.LENGTH_LONG).show();
    }
    private void login() {
        api = null; sessionId = null;
        page("AI Ассистент · Вход");
        EditText address = input("Адрес backend"); address.setText("http://127.0.0.1:8000");
        EditText credential = input("Токен от локального администратора");
        credential.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        credential.setImportantForAutofill(android.view.View.IMPORTANT_FOR_AUTOFILL_NO);
        button(content,"Войти",() -> {
            try {
                ApiClient candidate = new ApiClient(address.getText().toString().trim(),credential.getText().toString().trim());
                credential.setText("");
                request(() -> candidate.request("POST","auth/login",new JSONObject()), result -> {
                    api = candidate;
                    chat();
                    syncLocalReminders();
                });
            } catch (Exception e) { error(); }
        });
    }
    private void navButton(
        LinearLayout parent,
        String icon,
        String title,
        Runnable action
    ) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setPadding(dp(1), dp(5), dp(1), dp(5));
        box.setOnClickListener(v -> action.run());

        TextView iconView = new TextView(this);
        iconView.setText(icon);
        iconView.setTextSize(18);
        iconView.setGravity(Gravity.CENTER);
        iconView.setTextColor(getColor(R.color.primary));

        TextView titleView = new TextView(this);
        titleView.setText(title);
        titleView.setTextSize(9);
        titleView.setMaxLines(1);
        titleView.setGravity(Gravity.CENTER);
        titleView.setTextColor(getColor(R.color.text_secondary));

        box.addView(iconView);
        box.addView(titleView);

        parent.addView(
            box,
            new LinearLayout.LayoutParams(0, dp(64), 1)
        );
    }

    private void navigation() {
        LinearLayout nav = new LinearLayout(this);
        nav.setOrientation(LinearLayout.HORIZONTAL);
        nav.setGravity(Gravity.CENTER);
        nav.setBackgroundResource(R.drawable.bg_bottom_nav);
        nav.setPadding(dp(3), dp(2), dp(3), dp(2));

        navButton(nav, "\u25C9", "\u0427\u0430\u0442", this::chat);
        navButton(nav, "\u2726", "\u0418\u043d\u0441\u0442\u0440\u0443\u043c.", this::toolsPage);
        navButton(nav, "\u25F7", "\u0418\u0441\u0442\u043e\u0440\u0438\u044f", this::sessions);
        navButton(nav, "\u2699", "\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438", this::settingsPage);

        root.addView(nav);
    }

    private Button toolCard(String title, Runnable action) {
        Button card = new Button(this);
        card.setText(title);
        card.setTextColor(getColor(R.color.text_primary));
        card.setTextSize(14);
        card.setAllCaps(false);
        card.setGravity(Gravity.CENTER);
        card.setBackgroundResource(R.drawable.bg_card);
        card.setPadding(dp(8), dp(12), dp(8), dp(12));
        card.setOnClickListener(v -> action.run());
        return card;
    }

    private void toolRow(
        String leftTitle,
        Runnable leftAction,
        String rightTitle,
        Runnable rightAction
    ) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);

        Button left = toolCard(leftTitle, leftAction);
        Button right = toolCard(rightTitle, rightAction);

        LinearLayout.LayoutParams lp1 =
            new LinearLayout.LayoutParams(0, dp(112), 1);
        lp1.setMargins(0, dp(6), dp(6), dp(6));

        LinearLayout.LayoutParams lp2 =
            new LinearLayout.LayoutParams(0, dp(112), 1);
        lp2.setMargins(dp(6), dp(6), 0, dp(6));

        row.addView(left, lp1);
        row.addView(right, lp2);
        content.addView(row);
    }

    private void toolsPage() {
        page("\u0418\u043d\u0441\u0442\u0440\u0443\u043c\u0435\u043d\u0442\u044b");
        navigation();

        label(
            content,
            "\u0412\u0441\u0451 \u043d\u0443\u0436\u043d\u043e\u0435 \u0432 \u043e\u0434\u043d\u043e\u043c \u043c\u0435\u0441\u0442\u0435"
        );

        toolRow(
            "\u270E\n\u0417\u0430\u043c\u0435\u0442\u043a\u0438",
            this::notesPage,
            "\u23F0\n\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f",
            this::tasks
        );

        toolRow(
            "\u2713\n\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f",
            this::confirmations,
            "\u25C9\n\u0413\u043e\u043b\u043e\u0441",
            this::voicePage
        );

        toolRow(
            "\u2315\n\u041f\u043e\u0438\u0441\u043a",
            () -> Toast.makeText(this, "\u041f\u043e\u0434\u043a\u043b\u044e\u0447\u0438\u043c \u043f\u043e\u0437\u0436\u0435", Toast.LENGTH_SHORT).show(),
            "\u25A3\n\u0424\u0430\u0439\u043b\u044b",
            () -> Toast.makeText(this, "\u041f\u043e\u0434\u043a\u043b\u044e\u0447\u0438\u043c \u043f\u043e\u0437\u0436\u0435", Toast.LENGTH_SHORT).show()
        );

        toolRow(
            "\u25A7\n\u041a\u0430\u043b\u0435\u043d\u0434\u0430\u0440\u044c",
            () -> Toast.makeText(this, "\u041a\u0430\u043b\u0435\u043d\u0434\u0430\u0440\u044c \u0431\u0443\u0434\u0435\u0442 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0451\u043d", Toast.LENGTH_SHORT).show(),
            "</>\n\u041a\u043e\u0434",
            this::chat
        );
    }

    private void notesPage() {
        page("\u0417\u0430\u043c\u0435\u0442\u043a\u0438");
        navigation();

        label(
            content,
            "\u041b\u043e\u043a\u0430\u043b\u044c\u043d\u0430\u044f \u043f\u0430\u043c\u044f\u0442\u044c \u043d\u0430 \u0441\u043c\u0430\u0440\u0442\u0444\u043e\u043d\u0435"
        );

        EditText noteInput = new EditText(this);
        noteInput.setHint("\u041d\u0430\u043f\u0438\u0448\u0438 \u0437\u0430\u043c\u0435\u0442\u043a\u0443...");
        noteInput.setTextColor(getColor(R.color.text_primary));
        noteInput.setHintTextColor(getColor(R.color.text_secondary));
        noteInput.setTextSize(16);
        noteInput.setMinLines(3);
        noteInput.setPadding(dp(16), dp(14), dp(16), dp(14));
        noteInput.setBackgroundResource(R.drawable.bg_input);

        LinearLayout.LayoutParams inputParams =
            new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
            );

        inputParams.setMargins(0, dp(12), 0, dp(12));
        content.addView(noteInput, inputParams);

        button(
            content,
            "\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0437\u0430\u043c\u0435\u0442\u043a\u0443",
            () -> {
                String text = noteInput.getText().toString().trim();

                if (text.isEmpty()) {
                    Toast.makeText(
                        this,
                        "\u041d\u0430\u043f\u0438\u0448\u0438 \u0442\u0435\u043a\u0441\u0442 \u0437\u0430\u043c\u0435\u0442\u043a\u0438",
                        Toast.LENGTH_SHORT
                    ).show();
                    return;
                }

                try {
                    if (!Python.isStarted()) {
                        Python.start(new AndroidPlatform(this));
                    }

                    PyObject module =
                        Python.getInstance().getModule("mobile_bridge");

                    String result = module.callAttr(
                        "save_note",
                        getFilesDir().getAbsolutePath(),
                        text
                    ).toString();

                    if ("SAVED".equals(result)) {
                        Toast.makeText(
                            this,
                            "\u0417\u0430\u043c\u0435\u0442\u043a\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0430",
                            Toast.LENGTH_SHORT
                        ).show();

                        notesPage();
                    }

                } catch (Exception e) {
                    Toast.makeText(
                        this,
                        "Notes error: " + e.getClass().getSimpleName(),
                        Toast.LENGTH_LONG
                    ).show();
                }
            }
        );

        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(this));
            }

            PyObject module =
                Python.getInstance().getModule("mobile_bridge");

            String savedNotes = module.callAttr(
                "list_notes",
                getFilesDir().getAbsolutePath()
            ).toString();

            label(
                content,
                savedNotes.isEmpty()
                    ? "\u041f\u043e\u043a\u0430 \u043d\u0435\u0442 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0445 \u0437\u0430\u043c\u0435\u0442\u043e\u043a."
                    : savedNotes
            );

        } catch (Exception e) {
            label(content, "Notes DB error");
        }
    }

    private void voicePage() {
        page("\u0413\u043e\u043b\u043e\u0441\u043e\u0432\u043e\u0439 \u0440\u0435\u0436\u0438\u043c");
        navigation();

        LinearLayout center = new LinearLayout(this);
        center.setOrientation(LinearLayout.VERTICAL);
        center.setGravity(Gravity.CENTER);
        center.setPadding(0, dp(40), 0, dp(20));

        TextView mic = new TextView(this);
        mic.setText("\ud83c\udf99");
        mic.setTextSize(68);
        mic.setGravity(Gravity.CENTER);
        mic.setTextColor(Color.WHITE);
        mic.setBackgroundResource(R.drawable.bg_voice_orb);

        LinearLayout.LayoutParams micParams =
            new LinearLayout.LayoutParams(dp(180), dp(180));

        center.addView(mic, micParams);

        TextView status = new TextView(this);
        status.setText("\u041d\u0430\u0436\u043c\u0438, \u0447\u0442\u043e\u0431\u044b \u043d\u0430\u0447\u0430\u0442\u044c");
        status.setTextSize(22);
        status.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        status.setTextColor(getColor(R.color.text_primary));
        status.setGravity(Gravity.CENTER);
        status.setPadding(0, dp(28), 0, dp(10));

        center.addView(status);

        TextView hint = new TextView(this);
        hint.setText(
            "\u0413\u043e\u0432\u043e\u0440\u0438 \u0435\u0441\u0442\u0435\u0441\u0442\u0432\u0435\u043d\u043d\u043e.\n" +
            "\u0410\u0441\u0441\u0438\u0441\u0442\u0435\u043d\u0442 \u043f\u0440\u0435\u0432\u0440\u0430\u0442\u0438\u0442 \u0440\u0435\u0447\u044c \u0432 \u0442\u0435\u043a\u0441\u0442."
        );
        hint.setTextSize(15);
        hint.setTextColor(getColor(R.color.text_secondary));
        hint.setGravity(Gravity.CENTER);

        center.addView(hint);

        content.addView(
            center,
            new LinearLayout.LayoutParams(-1, -2)
        );

        mic.setOnClickListener(v -> {
            status.setText("\u042f \u0441\u043b\u0443\u0448\u0430\u044e...");

            voice.listen(new VoiceProvider.Callback() {
                public void onText(String value) {
                    runOnUiThread(() -> {
                        status.setText("\u0423\u0441\u043b\u044b\u0448\u0430\u043b: " + value);

                        Toast.makeText(
                            MainActivity.this,
                            value,
                            Toast.LENGTH_LONG
                        ).show();
                    });
                }

                public void onError(String message) {
                    runOnUiThread(() -> {
                        status.setText(
                            "\u0413\u043e\u043b\u043e\u0441 \u043f\u043e\u043a\u0430 \u043d\u0435 \u043f\u043e\u0434\u043a\u043b\u044e\u0447\u0451\u043d"
                        );

                        Toast.makeText(
                            MainActivity.this,
                            message,
                            Toast.LENGTH_SHORT
                        ).show();
                    });
                }
            });
        });
    }

    private void settingsPage() {
        page("\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438");
        navigation();

        label(content, "\u25cf  Online");
        label(content, "\u0422\u0435\u043c\u0430: Dark Neon");
        label(content, "\u0420\u0435\u0436\u0438\u043c: \u0430\u0432\u0442\u043e\u043d\u043e\u043c\u043d\u044b\u0439");
        label(content, "\u041f\u0430\u043c\u044f\u0442\u044c: SQLite \u043d\u0430 \u0441\u043c\u0430\u0440\u0442\u0444\u043e\u043d\u0435");

        boolean keySaved = SecureStore.hasApiKey(this);

        label(
            content,
            keySaved
                ? "OpenRouter: \u043a\u043b\u044e\u0447 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d"
                : "OpenRouter: \u043a\u043b\u044e\u0447 \u043d\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d"
        );

        EditText apiKey = input("OpenRouter API key");
        apiKey.setInputType(
            InputType.TYPE_CLASS_TEXT |
            InputType.TYPE_TEXT_VARIATION_PASSWORD
        );
        apiKey.setImportantForAutofill(
            android.view.View.IMPORTANT_FOR_AUTOFILL_NO
        );

        button(
            content,
            "\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c OpenRouter \u043a\u043b\u044e\u0447",
            () -> {
                String value = apiKey.getText().toString().trim();

                if (value.isEmpty()) {
                    Toast.makeText(
                        this,
                        "\u0412\u0432\u0435\u0434\u0438 OpenRouter API key",
                        Toast.LENGTH_SHORT
                    ).show();
                    return;
                }

                try {
                    SecureStore.saveApiKey(this, value);
                    apiKey.setText("");

                    Toast.makeText(
                        this,
                        "\u041a\u043b\u044e\u0447 \u0437\u0430\u0449\u0438\u0449\u0451\u043d \u0438 \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d",
                        Toast.LENGTH_SHORT
                    ).show();

                    settingsPage();

                } catch (Exception e) {
                    Toast.makeText(
                        this,
                        "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u043a\u043b\u044e\u0447",
                        Toast.LENGTH_LONG
                    ).show();
                }
            }
        );
    }

    private void chat() {
        page("\u0427\u0430\u0442");

        boolean localHistoryFound = false;

        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(this));
            }

            PyObject module =
                Python.getInstance().getModule("mobile_bridge");

            String historyJson = module.callAttr(
                "list_chat_history",
                getFilesDir().getAbsolutePath(),
                40
            ).toString();

            JSONArray savedHistory = new JSONArray(historyJson);

            for (int i = 0; i < savedHistory.length(); i++) {
                JSONObject item = savedHistory.getJSONObject(i);

                boolean isUser =
                    "user".equals(item.optString("role"));

                messageBubble(
                    item.optString("content"),
                    isUser
                );
            }

            localHistoryFound = savedHistory.length() > 0;

        } catch (Exception ignored) {
        }

        if (!localHistoryFound) {
            messageBubble(
                "\u041f\u0440\u0438\u0432\u0435\u0442! \ud83d\udc4b\n" +
                "\u042f \u0442\u0432\u043e\u0439 AI-\u0430\u0441\u0441\u0438\u0441\u0442\u0435\u043d\u0442.\n\n" +
                "\u042f \u043c\u043e\u0433\u0443 \u043e\u0442\u0432\u0435\u0447\u0430\u0442\u044c \u043d\u0430 \u0432\u043e\u043f\u0440\u043e\u0441\u044b, " +
                "\u0437\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u0442\u044c \u0437\u0430\u043c\u0435\u0442\u043a\u0438 \u0438 " +
                "\u0441\u0442\u0430\u0432\u0438\u0442\u044c \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f.\n\n" +
                "\u0427\u0435\u043c \u043c\u043e\u0433\u0443 \u043f\u043e\u043c\u043e\u0447\u044c?",
                false
            );
        }

        LinearLayout composer = new LinearLayout(this);
        composer.setOrientation(LinearLayout.HORIZONTAL);
        composer.setGravity(Gravity.CENTER_VERTICAL);
        composer.setBackgroundResource(R.drawable.bg_card);
        composer.setPadding(dp(8), dp(7), dp(8), dp(7));

        EditText text = new EditText(this);
        text.setHint("\u041d\u0430\u043f\u0438\u0448\u0438 \u0441\u043e\u043e\u0431\u0449\u0435\u043d\u0438\u0435...");
        text.setTextColor(getColor(R.color.text_primary));
        text.setHintTextColor(getColor(R.color.text_muted));
        text.setTextSize(16);
        text.setMaxLines(4);
        text.setBackgroundResource(R.drawable.bg_input);
        text.setPadding(dp(14), dp(10), dp(14), dp(10));

        composer.addView(
            text,
            new LinearLayout.LayoutParams(0, -2, 1)
        );

        TextView mic = new TextView(this);
        mic.setText("\ud83c\udf99");
        mic.setTextSize(23);
        mic.setGravity(Gravity.CENTER);
        mic.setTextColor(getColor(R.color.primary));
        mic.setPadding(dp(8), dp(8), dp(8), dp(8));

        LinearLayout.LayoutParams iconParams =
            new LinearLayout.LayoutParams(dp(50), dp(50));
        iconParams.setMargins(dp(6), 0, dp(4), 0);

        composer.addView(mic, iconParams);

        TextView send = new TextView(this);
        send.setText("\u27A4");
        send.setTextSize(23);
        send.setGravity(Gravity.CENTER);
        send.setTextColor(Color.WHITE);
        send.setBackgroundResource(R.drawable.bg_primary_button);

        composer.addView(
            send,
            new LinearLayout.LayoutParams(dp(50), dp(50))
        );

        root.addView(
            composer,
            new LinearLayout.LayoutParams(-1, -2)
        );

        navigation();

        Runnable sendMessage = () -> {
            String message = text.getText().toString().trim();

            if (message.isBlank()) {
                return;
            }

            text.setText("");
            messageBubble(message, true);

            request(() -> {
                String apiKey = SecureStore.getApiKey(this);

                if (apiKey.isEmpty()) {
                    return new JSONObject()
                        .put(
                            "reply",
                            "OpenRouter ???? ?? ????????. ?????? ?????????."
                        );
                }

                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(this));
                }

                PyObject module =
                    Python.getInstance().getModule("mobile_bridge");

                String raw = module.callAttr(
                    "mobile_ai_turn",
                    apiKey,
                    message,
                    getFilesDir().getAbsolutePath()
                ).toString();

                return new JSONObject(raw);

            }, value -> {
                String assistantReply =
                    value.getString("reply");

                messageBubble(
                    assistantReply,
                    false
                );

                if (speakNextReply) {
                    speakNextReply = false;

                    if (voice != null) {
                        voice.speak(assistantReply);
                    }
                }

                JSONObject reminder =
                    value.optJSONObject("reminder");

                if (reminder != null) {
                    try {
                        scheduleLocalReminder(reminder);
                    } catch (Exception e) {
                        Toast.makeText(
                            this,
                            "\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e, \u043d\u043e AlarmManager \u043d\u0435 \u0437\u0430\u043f\u0443\u0449\u0435\u043d",
                            Toast.LENGTH_LONG
                        ).show();
                    }
                }
            });
        };

        send.setOnClickListener(v -> sendMessage.run());

        mic.setOnClickListener(v ->
            voice.listen(new VoiceProvider.Callback() {
                public void onText(String value) {
                    runOnUiThread(() -> {
                        speakNextReply = true;
                        text.setText(value);
                        sendMessage.run();
                    });
                }

                public void onError(String message) {
                    runOnUiThread(() ->
                        Toast.makeText(
                            MainActivity.this,
                            message,
                            Toast.LENGTH_SHORT
                        ).show()
                    );
                }
            })
        );


    }

    private String prettyTime(String value) {
        try {
            java.time.LocalDateTime local;

            try {
                OffsetDateTime date = OffsetDateTime.parse(value);

                local = date.toInstant()
                    .atZone(ZoneId.systemDefault())
                    .toLocalDateTime();

            } catch (Exception first) {
                local = java.time.LocalDateTime.parse(value);
            }

            LocalDate today = LocalDate.now();

            if (local.toLocalDate().equals(today)) {
                return "\u0421\u0435\u0433\u043e\u0434\u043d\u044f " +
                    local.format(
                        DateTimeFormatter.ofPattern("HH:mm")
                    );
            }

            return local.format(
                DateTimeFormatter.ofPattern("dd.MM.yyyy  HH:mm")
            );

        } catch (Exception e) {
            return value;
        }
    }

    private void sessions() {
        page("\u0418\u0441\u0442\u043e\u0440\u0438\u044f");
        navigation();

        button(
            content,
            "\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0447\u0430\u0442",
            this::chat
        );

        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(this));
            }

            PyObject module =
                Python.getInstance().getModule("mobile_bridge");

            String historyJson = module.callAttr(
                "list_chat_history",
                getFilesDir().getAbsolutePath(),
                100
            ).toString();

            JSONArray items = new JSONArray(historyJson);

            if (items.length() == 0) {
                label(
                    content,
                    "\u0418\u0441\u0442\u043e\u0440\u0438\u044f \u043f\u043e\u043a\u0430 \u043f\u0443\u0441\u0442\u0430"
                );
                return;
            }

            for (int i = 0; i < items.length(); i++) {
                JSONObject item = items.getJSONObject(i);

                boolean isUser =
                    "user".equals(item.optString("role"));

                String time = prettyTime(
                    item.optString("created_at")
                );

                TextView meta = new TextView(this);
                meta.setText(
                    (isUser
                        ? "\u0412\u044b"
                        : "AI") +
                    " \u00b7 " + time
                );

                meta.setTextSize(12);
                meta.setTextColor(
                    getColor(R.color.text_secondary)
                );

                meta.setPadding(
                    dp(8),
                    dp(10),
                    dp(8),
                    dp(3)
                );

                content.addView(meta);

                messageBubble(
                    item.optString("content"),
                    isUser
                );
            }

        } catch (Exception e) {
            label(
                content,
                "\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043f\u0440\u043e\u0447\u0438\u0442\u0430\u0442\u044c \u043b\u043e\u043a\u0430\u043b\u044c\u043d\u0443\u044e \u0438\u0441\u0442\u043e\u0440\u0438\u044e"
            );
        }
    }

    private void confirmations() {
        page("Подтверждения"); navigation(); ApiClient client=api;
        request(() -> client.request("GET","confirmations",null),value -> {
            JSONArray items=value.getJSONArray("items");
            if(items.length()==0) label(content,"Нет ожидающих действий");
            for(int i=0;i<items.length();i++) {
                Models.Confirmation item=new Models.Confirmation(items.getJSONObject(i));
                label(content,item.tool+" · "+item.status+"\n"+item.arguments+"\nДо: "+item.expires);
                String action="approved".equals(item.status)?"execute":"approve";
                button(content,action.equals("approve")?"Одобрить":"Выполнить один раз",
                    () -> request(() -> client.request("POST","confirmations/"+item.id+"/"+action,new JSONObject()),r -> confirmations()));
                button(content,"Отменить",() -> request(() -> client.request("POST","confirmations/"+item.id+"/cancel",new JSONObject()),r -> confirmations()));
            }
        });
    }
    private void scheduleLocalReminder(JSONObject item) throws Exception {
        if (!"reminders.notify".equals(item.optString("tool"))) return;
        if (!item.optBoolean("enabled", false)) return;

        long triggerAt = OffsetDateTime
            .parse(item.getString("next_run_at"))
            .toInstant()
            .toEpochMilli();

        if (triggerAt <= System.currentTimeMillis()) return;

        JSONObject arguments = item.optJSONObject("arguments");
        String reminderText = arguments != null
            ? arguments.optString("text", "\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435")
            : "\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435";

        String taskId = item.getString("task_id");

        Intent intent = new Intent(this, ReminderReceiver.class);
        intent.putExtra("text", reminderText);
        intent.putExtra("task_id", taskId);

        PendingIntent pending = PendingIntent.getBroadcast(
            this,
            taskId.hashCode(),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        AlarmManager alarms =
            (AlarmManager) getSystemService(ALARM_SERVICE);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
            !alarms.canScheduleExactAlarms()) {

            Toast.makeText(
                this,
                "\u0420\u0430\u0437\u0440\u0435\u0448\u0438 \u0442\u043e\u0447\u043d\u044b\u0435 \u0431\u0443\u0434\u0438\u043b\u044c\u043d\u0438\u043a\u0438 \u0434\u043b\u044f AI \u0410\u0441\u0441\u0438\u0441\u0442\u0435\u043d\u0442\u0430",
                Toast.LENGTH_LONG
            ).show();

            Intent settingsIntent = new Intent(
                android.provider.Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
                android.net.Uri.parse("package:" + getPackageName())
            );

            startActivity(settingsIntent);
            return;
        }

        alarms.setExactAndAllowWhileIdle(
            AlarmManager.RTC_WAKEUP,
            triggerAt,
            pending
        );
    }

    private void syncLocalReminders() {
        worker.submit(() -> {
            try {
                if (!Python.isStarted()) {
                    Python.start(new AndroidPlatform(this));
                }

                PyObject module =
                    Python.getInstance().getModule("mobile_bridge");

                String raw = module.callAttr(
                    "list_local_reminders",
                    getFilesDir().getAbsolutePath()
                ).toString();

                JSONArray items = new JSONArray(raw);

                for (int i = 0; i < items.length(); i++) {
                    scheduleLocalReminder(
                        items.getJSONObject(i)
                    );
                }

            } catch (Exception ignored) {
            }
        });
    }


    private void cancelLocalReminder(String taskId) {
        Intent intent =
            new Intent(this, ReminderReceiver.class);

        PendingIntent pending =
            PendingIntent.getBroadcast(
                this,
                taskId.hashCode(),
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT |
                PendingIntent.FLAG_IMMUTABLE
            );

        AlarmManager alarms =
            (AlarmManager) getSystemService(ALARM_SERVICE);

        alarms.cancel(pending);
        pending.cancel();
    }

    private void tasks() {
        page("\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f");
        navigation();

        EditText text = input(
            "\u0422\u0435\u043a\u0441\u0442 \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f"
        );

        EditText due = input(
            "\u0412\u0440\u0435\u043c\u044f ISO 8601, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440 2030-01-01T09:00:00+03:00"
        );

        button(
            content,
            "\u0421\u043e\u0437\u0434\u0430\u0442\u044c",
            () -> {
                String message =
                    text.getText().toString().trim();

                String date =
                    due.getText().toString().trim();

                request(() -> {
                    if (!Python.isStarted()) {
                        Python.start(
                            new AndroidPlatform(this)
                        );
                    }

                    PyObject module =
                        Python.getInstance()
                            .getModule("mobile_bridge");

                    String raw = module.callAttr(
                        "create_local_reminder",
                        getFilesDir().getAbsolutePath(),
                        message,
                        date
                    ).toString();

                    JSONObject item =
                        new JSONObject(raw);

                    if (!"ok".equals(
                            item.optString("status"))) {

                        return new JSONObject()
                            .put(
                                "error",
                                item.optString("error")
                            );
                    }

                    scheduleLocalReminder(item);

                    return new JSONObject()
                        .put("ok", true);

                }, value -> {
                    if (value.has("error")) {
                        Toast.makeText(
                            this,
                            "\u041d\u0435\u0432\u0435\u0440\u043d\u043e\u0435 \u0432\u0440\u0435\u043c\u044f \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f",
                            Toast.LENGTH_LONG
                        ).show();
                    }

                    tasks();
                });
            }
        );

        try {
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(this));
            }

            PyObject module =
                Python.getInstance()
                    .getModule("mobile_bridge");

            String raw = module.callAttr(
                "list_local_reminders",
                getFilesDir().getAbsolutePath()
            ).toString();

            JSONArray items = new JSONArray(raw);

            if (items.length() == 0) {
                label(
                    content,
                    "\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0439 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442"
                );
            }

            for (int i = 0; i < items.length(); i++) {
                JSONObject item =
                    items.getJSONObject(i);

                String taskId =
                    item.getString("task_id");

                boolean enabled =
                    item.optBoolean("enabled", false);

                JSONObject arguments =
                    item.optJSONObject("arguments");

                String reminderText =
                    arguments != null
                        ? arguments.optString("text")
                        : "";

                label(
                    content,
                    reminderText +
                    "\n" +
                    prettyTime(
                        item.getString("next_run_at")
                    )
                );

                button(
                    content,
                    enabled
                        ? "\u041e\u0442\u043a\u043b\u044e\u0447\u0438\u0442\u044c"
                        : "\u0412\u043a\u043b\u044e\u0447\u0438\u0442\u044c",
                    () -> request(() -> {
                        PyObject m =
                            Python.getInstance()
                                .getModule("mobile_bridge");

                        String result = m.callAttr(
                            "set_local_reminder_enabled",
                            getFilesDir().getAbsolutePath(),
                            taskId,
                            !enabled
                        ).toString();

                        JSONObject updated =
                            new JSONObject(result);

                        if (!enabled) {
                            scheduleLocalReminder(updated);
                        } else {
                            cancelLocalReminder(taskId);
                        }

                        return new JSONObject()
                            .put("ok", true);

                    }, value -> tasks())
                );
            }

        } catch (Exception e) {
            label(
                content,
                "\u041e\u0448\u0438\u0431\u043a\u0430 \u043b\u043e\u043a\u0430\u043b\u044c\u043d\u044b\u0445 \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0439"
            );
        }
    }

    @Override protected void onDestroy() { generation++; voice.close(); worker.shutdownNow(); super.onDestroy(); }
}
