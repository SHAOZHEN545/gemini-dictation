package com.geminidictation.keyboard;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;

/**
 * Empty foreground service that keeps the keyboard process running with network access while a
 * recording uploads with the screen off or the keyboard hidden. The work itself runs in
 * {@link DictationJob}. Used on the main thread only.
 */
public final class TranscriptionService extends Service {
    private static final String CHANNEL = "transcription";
    private static TranscriptionService active;

    static void start(Context context) {
        try {
            context.startForegroundService(new Intent(context, TranscriptionService.class));
        } catch (RuntimeException ignored) {
            // The upload still runs; it is just more likely to be paused by the system.
        }
    }

    static void stopIfIdle() {
        if (active != null && !DictationJob.isRunning()) active.stopSelf();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager notifications = getSystemService(NotificationManager.class);
        notifications.createNotificationChannel(new NotificationChannel(
                CHANNEL, "后台转写", NotificationManager.IMPORTANCE_LOW));
        Notification notification = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_upload)
                .setContentTitle("正在转写录音")
                .setContentText("锁屏或收起键盘不会中断；完成后回到键盘查看")
                .setOngoing(true)
                .build();
        // startForeground must always run after startForegroundService, even if the job is done.
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(1, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
            } else startForeground(1, notification);
        } catch (RuntimeException refused) {
            stopSelf(startId);
            return START_NOT_STICKY;
        }
        active = this;
        if (!DictationJob.isRunning()) stopSelf(startId);
        return START_NOT_STICKY;
    }

    @Override public void onDestroy() {
        if (active == this) active = null;
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
