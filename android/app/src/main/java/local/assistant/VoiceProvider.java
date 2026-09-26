package local.assistant;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;

import java.util.ArrayList;
import java.util.Locale;

public interface VoiceProvider {
    interface Callback {
        void onText(String text);
        void onError(String message);
    }

    void listen(Callback callback);
    void speak(String text);
    void close();

    final class AndroidSpeech implements VoiceProvider {
        private final Activity activity;
        private SpeechRecognizer recognizer;
        private Callback callback;
        private TextToSpeech tts;
        private volatile boolean ttsReady;

        public AndroidSpeech(Activity activity) {
            this.activity = activity;

            tts = new TextToSpeech(activity, status -> {
                if (status == TextToSpeech.SUCCESS) {
                    int result = tts.setLanguage(
                        new Locale("ru", "RU")
                    );

                    ttsReady =
                        result != TextToSpeech.LANG_MISSING_DATA &&
                        result != TextToSpeech.LANG_NOT_SUPPORTED;

                    if (ttsReady) {
                        tts.setSpeechRate(1.0f);
                        tts.setPitch(1.0f);
                    }
                }
            });
        }

        @Override
        public void listen(Callback callback) {
            this.callback = callback;

            if (activity.checkSelfPermission(
                    Manifest.permission.RECORD_AUDIO
                ) != PackageManager.PERMISSION_GRANTED) {

                callback.onError(
                    "ет разрешения на использование микрофона"
                );
                return;
            }

            if (!SpeechRecognizer.isRecognitionAvailable(activity)) {
                callback.onError(
                    "аспознавание речи недоступно на этом устройстве"
                );
                return;
            }

            if (recognizer != null) {
                recognizer.destroy();
            }

            recognizer =
                SpeechRecognizer.createSpeechRecognizer(activity);

            recognizer.setRecognitionListener(
                new RecognitionListener() {
                    @Override
                    public void onReadyForSpeech(Bundle params) {
                    }

                    @Override
                    public void onBeginningOfSpeech() {
                    }

                    @Override
                    public void onRmsChanged(float rmsdB) {
                    }

                    @Override
                    public void onBufferReceived(byte[] buffer) {
                    }

                    @Override
                    public void onEndOfSpeech() {
                    }

                    @Override
                    public void onError(int error) {
                        if (AndroidSpeech.this.callback != null) {
                            AndroidSpeech.this.callback.onError(
                                errorMessage(error)
                            );
                        }
                    }

                    @Override
                    public void onResults(Bundle results) {
                        ArrayList<String> matches =
                            results.getStringArrayList(
                                SpeechRecognizer.RESULTS_RECOGNITION
                            );

                        if (
                            matches != null &&
                            !matches.isEmpty() &&
                            AndroidSpeech.this.callback != null
                        ) {
                            AndroidSpeech.this.callback.onText(
                                matches.get(0)
                            );
                        } else if (
                            AndroidSpeech.this.callback != null
                        ) {
                            AndroidSpeech.this.callback.onError(
                                "ечь не распознана"
                            );
                        }
                    }

                    @Override
                    public void onPartialResults(
                        Bundle partialResults
                    ) {
                    }

                    @Override
                    public void onEvent(
                        int eventType,
                        Bundle params
                    ) {
                    }
                }
            );

            Intent intent =
                new Intent(
                    RecognizerIntent.ACTION_RECOGNIZE_SPEECH
                );

            intent.putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM
            );

            intent.putExtra(
                RecognizerIntent.EXTRA_LANGUAGE,
                "ru-RU"
            );

            intent.putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE,
                "ru-RU"
            );

            intent.putExtra(
                RecognizerIntent.EXTRA_PARTIAL_RESULTS,
                false
            );

            intent.putExtra(
                RecognizerIntent.EXTRA_MAX_RESULTS,
                3
            );

            recognizer.startListening(intent);
        }

        private String errorMessage(int error) {
            switch (error) {
                case SpeechRecognizer.ERROR_AUDIO:
                    return "шибка микрофона";

                case SpeechRecognizer.ERROR_CLIENT:
                    return "шибка голосового модуля";

                case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS:
                    return "ет разрешения на микрофон";

                case SpeechRecognizer.ERROR_NETWORK:
                case SpeechRecognizer.ERROR_NETWORK_TIMEOUT:
                    return "шибка сети при распознавании речи";

                case SpeechRecognizer.ERROR_NO_MATCH:
                    return "е удалось распознать речь";

                case SpeechRecognizer.ERROR_RECOGNIZER_BUSY:
                    return "икрофон уже используется";

                case SpeechRecognizer.ERROR_SERVER:
                    return "Сервис распознавания временно недоступен";

                case SpeechRecognizer.ERROR_SPEECH_TIMEOUT:
                    return "ечь не обнаружена";

                default:
                    return "шибка распознавания речи: " + error;
            }
        }

        @Override
        public void speak(String text) {
            if (
                tts != null &&
                ttsReady &&
                text != null &&
                !text.isBlank()
            ) {
                tts.speak(
                    text,
                    TextToSpeech.QUEUE_FLUSH,
                    null,
                    "assistant_reply"
                );
            }
        }

        @Override
        public void close() {
            if (recognizer != null) {
                recognizer.cancel();
                recognizer.destroy();
                recognizer = null;
            }

            if (tts != null) {
                tts.stop();
                tts.shutdown();
                tts = null;
            }

            ttsReady = false;
        }
    }

    class Unavailable implements VoiceProvider {
        public void listen(Callback callback) {
            callback.onError(
                "Speech recognition is not configured"
            );
        }

        public void speak(String text) {
        }

        public void close() {
        }
    }
}
