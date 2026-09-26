package local.assistant;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public final class ReminderReceiver extends BroadcastReceiver {
    private static final String CHANNEL_ID =
        "assistant_reminders";

    @Override
    public void onReceive(
        Context context,
        Intent intent
    ) {
        String text =
            intent.getStringExtra("text");

        String taskId =
            intent.getStringExtra("task_id");

        if (text == null || text.isBlank()) {
            text =
                "\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u0435";
        }

        NotificationManager manager =
            (NotificationManager)
                context.getSystemService(
                    Context.NOTIFICATION_SERVICE
                );

        NotificationChannel channel =
            new NotificationChannel(
                CHANNEL_ID,
                "\u041d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f AI \u0430\u0441\u0441\u0438\u0441\u0442\u0435\u043d\u0442\u0430",
                NotificationManager.IMPORTANCE_HIGH
            );

        channel.setDescription(
            "\u041b\u043e\u043a\u0430\u043b\u044c\u043d\u044b\u0435 \u043d\u0430\u043f\u043e\u043c\u0438\u043d\u0430\u043d\u0438\u044f \u0430\u0441\u0441\u0438\u0441\u0442\u0435\u043d\u0442\u0430"
        );

        manager.createNotificationChannel(channel);

        Intent openApp =
            new Intent(context, MainActivity.class);

        openApp.setFlags(
            Intent.FLAG_ACTIVITY_NEW_TASK |
            Intent.FLAG_ACTIVITY_CLEAR_TOP
        );

        PendingIntent openPending =
            PendingIntent.getActivity(
                context,
                0,
                openApp,
                PendingIntent.FLAG_UPDATE_CURRENT |
                PendingIntent.FLAG_IMMUTABLE
            );

        Notification notification =
            new Notification.Builder(
                context,
                CHANNEL_ID
            )
            .setSmallIcon(
                android.R.drawable.ic_dialog_info
            )
            .setContentTitle(
                "AI \u0410\u0441\u0441\u0438\u0441\u0442\u0435\u043d\u0442"
            )
            .setContentText(text)
            .setStyle(
                new Notification.BigTextStyle()
                    .bigText(text)
            )
            .setContentIntent(openPending)
            .setAutoCancel(true)
            .build();

        int id =
            taskId != null
                ? taskId.hashCode()
                : (int) System.currentTimeMillis();

        manager.notify(id, notification);
    }
}
