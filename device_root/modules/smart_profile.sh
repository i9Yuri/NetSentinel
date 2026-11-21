#!/system/bin/sh
# Carregado pelo watchdog e pela CLI de perfil via ADB.
portable_packages='com.dywx.larkplayer
org.swiftapps.swiftbackup
org.lineageos.aperture'
permanent_bloat='com.caf.fmradio
org.lineageos.recorder
com.android.dreams.basic
com.android.dreams.phototable
com.android.cellbroadcastreceiver
com.android.cellbroadcastreceiver.module
com.android.cellbroadcastservice
com.android.stk
com.android.messaging
com.android.dialer
com.android.calllogbackup
com.android.mms.service
com.android.carrierdefaultapp
com.android.printspooler
com.android.bips
com.android.printservice.recommendation'
sentry_packages="$portable_packages
$permanent_bloat"

disable_profile_packages() (
    installed=$(pm list packages -U --user 0) || exit 1
    failed=0
    for package do
        uid=$(printf '%s\n' "$installed" | awk -v name="package:$package" '
            $1 == name && $2 ~ /^uid:[0-9]+$/ { print substr($2, 5); exit }')
        [ -n "$uid" ] || continue
        pm disable-user --user 0 "$package" || failed=1
        am force-stop --user 0 "$package" || failed=1
        case "$package" in
            com.dywx.larkplayer|org.swiftapps.swiftbackup)
                name=$(printf '%s' "$package" | sed 's/\./[.]/g')
                pkill -KILL -u "$uid" -f "^$name(:[[:alnum:]_.]+)?\$" || [ "$?" = 1 ] || failed=1 ;;
        esac
    done
    exit "$failed"
)

apply_permanent_debloat() {
    disable_profile_packages $permanent_bloat
}

apply_sentry_mode() {
    disable_profile_packages $sentry_packages
}

apply_portable_mode() (
    failed=0
    apply_permanent_debloat || failed=1
    installed=$(pm list packages --user 0) || exit 1
    for package in $portable_packages; do
        if printf '%s\n' "$installed" | grep -qxF "package:$package"; then
            pm enable --user 0 "$package" || failed=1
        fi
    done
    exit "$failed"
)

with_profile_lock() (
    umask 077
    lock_file="$base/data/profile.lock"
    mkdir -p -- "$(dirname -- "$lock_file")"
    "$@"
)

sync_profile_with_power() {
    case "${1:-}" in
        1) with_profile_lock apply_sentry_mode ;;
        0) with_profile_lock apply_portable_mode ;;
        *) return 2 ;;
    esac
}

profile_package_status() (
    enabled=$(pm list packages -e --user 0) || exit 1
    disabled=$(pm list packages -d --user 0) || exit 1
    for package in $sentry_packages; do
        if printf '%s\n' "$enabled" | grep -qxF "package:$package"; then
            state=habilitado
        elif printf '%s\n' "$disabled" | grep -qxF "package:$package"; then
            state=desabilitado
        else
            state=ausente
        fi
        printf '%-42s %s\n' "$package" "$state"
    done
)

show_profile_status() {
    with_profile_lock profile_package_status
}
