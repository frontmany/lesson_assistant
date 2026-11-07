// Звонок урока на LiveKit.
// SDK подключается в HTML: livekit-client.umd.js даёт глобальный LivekitClient.

import { LESSON_ID, api } from "./lesson.js";

const { DisconnectReason, Room, RoomEvent, Track } = window.LivekitClient;

export { DisconnectReason };

// Войти в звонок, включить микрофон и слышать остальных.
// Вызывать только по нажатию кнопки: без жеста пользователя браузер
// не даст ни микрофон, ни звук. mic: false — войти с выключенным микрофоном,
// cam: true — сразу включить камеру; без камеры урок идёт, поэтому её ошибка не мешает входу.
export async function joinCall(participantId, { onSpeakers, onState, mic = true, cam = false } = {}) {
  // участника сервер берёт из сессии (#24); participantId — для проверки, что это тот же человек
  const { url, token, participant_id: id } = await api(`/api/lessons/${LESSON_ID}/join`);
  if (participantId && id !== participantId) throw new Error("Нет доступа к уроку");

  const room = new Room({ adaptiveStream: true, dynacast: true });

  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind === Track.Kind.Audio) document.body.append(track.attach());
  });
  room.on(RoomEvent.TrackUnsubscribed, (track) => track.detach().forEach((el) => el.remove()));
  room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => onSpeakers?.(speakers.map((p) => p.identity)));
  // «Отключились» приходит отдельным событием: в нём причина, например вход в урок из другого окна
  room.on(RoomEvent.ConnectionStateChanged, (state) => state !== "disconnected" && onState?.(state));
  room.on(RoomEvent.Disconnected, (reason) => onState?.("disconnected", reason));

  await room.connect(url, token);
  try {
    await room.startAudio();
    if (mic) await room.localParticipant.setMicrophoneEnabled(true);
  } catch (err) {
    // Без микрофона в звонке не остаёмся: иначе после повторного входа будет два подключения
    await room.disconnect();
    throw err;
  }
  if (cam) {
    try {
      await room.localParticipant.setCameraEnabled(true);
    } catch (err) {
      console.warn("камера не включилась", err);
    }
  }
  return room;
}

export async function toggleMic(room) {
  const enabled = room.localParticipant.isMicrophoneEnabled;
  await room.localParticipant.setMicrophoneEnabled(!enabled);
  return !enabled;
}
