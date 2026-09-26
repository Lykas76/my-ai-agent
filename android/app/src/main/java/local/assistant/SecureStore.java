package local.assistant;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

public final class SecureStore {
    private static final String ALIAS = "assistant_openrouter_key_v1";
    private static final String PREFS = "assistant_secure";
    private static final String KEY_VALUE = "openrouter_key";
    private static final String KEY_IV = "openrouter_iv";

    private SecureStore() {}

    private static SecretKey getOrCreateKey() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);

        if (store.containsAlias(ALIAS)) {
            return ((KeyStore.SecretKeyEntry)
                store.getEntry(ALIAS, null)).getSecretKey();
        }

        KeyGenerator generator = KeyGenerator.getInstance(
            KeyProperties.KEY_ALGORITHM_AES,
            "AndroidKeyStore"
        );

        generator.init(
            new KeyGenParameterSpec.Builder(
                ALIAS,
                KeyProperties.PURPOSE_ENCRYPT |
                KeyProperties.PURPOSE_DECRYPT
            )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .build()
        );

        return generator.generateKey();
    }

    public static void saveApiKey(Context context, String value)
        throws Exception {

        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, getOrCreateKey());

        byte[] encrypted = cipher.doFinal(
            value.getBytes(StandardCharsets.UTF_8)
        );

        SharedPreferences prefs = context.getSharedPreferences(
            PREFS,
            Context.MODE_PRIVATE
        );

        prefs.edit()
            .putString(
                KEY_VALUE,
                Base64.encodeToString(encrypted, Base64.NO_WRAP)
            )
            .putString(
                KEY_IV,
                Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP)
            )
            .apply();
    }

    public static String getApiKey(Context context) throws Exception {
        SharedPreferences prefs = context.getSharedPreferences(
            PREFS,
            Context.MODE_PRIVATE
        );

        String encrypted = prefs.getString(KEY_VALUE, "");
        String iv = prefs.getString(KEY_IV, "");

        if (encrypted.isEmpty() || iv.isEmpty()) {
            return "";
        }

        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");

        cipher.init(
            Cipher.DECRYPT_MODE,
            getOrCreateKey(),
            new GCMParameterSpec(
                128,
                Base64.decode(iv, Base64.NO_WRAP)
            )
        );

        byte[] plain = cipher.doFinal(
            Base64.decode(encrypted, Base64.NO_WRAP)
        );

        return new String(plain, StandardCharsets.UTF_8);
    }

    public static boolean hasApiKey(Context context) {
        try {
            return !getApiKey(context).isEmpty();
        } catch (Exception e) {
            return false;
        }
    }

    public static void clearApiKey(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            .edit()
            .remove(KEY_VALUE)
            .remove(KEY_IV)
            .apply();
    }
}
