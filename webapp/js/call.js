// Звонок урока на LiveKit. Код: Фёдор.
// SDK подключается в HTML: livekit-client.umd.js даёт глобальный LivekitClient.

import { LESSON_ID } from "./lesson.js";

const { Room, RoomEvent, Track } = window.LivekitClient;

// Войти в звонок, включить микрофон и слышать остальных.
// Вызывать только по нажатию кнопки: без жеста пользователя браузер
// не даст ни микрофон, ни звук.
export async function joinCall(participantId, { onSpeakers, onState } = {}) {
  const res = await fetch(`/api/lessons/${LESSON_ID}/join?participant_id=${encodeURIComponent(participantId)}`);
  if (!res.ok) throw new Error("Нет доступа к уроку");
  const { url, token } = await res.json();

  const room = new Room({ adaptiveStream: true, dynacast: true });

  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind === Track.Kind.Audio) document.body.append(track.attach());
  });
  room.on(RoomEvent.TrackUnsubscribed, (track) => track.detach().forEach((el) => el.remove()));
  room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => onSpeakers?.(speakers.map((p) => p.identity)));
  room.on(RoomEvent.ConnectionStateChanged, (state) => onState?.(state));

  await room.connect(url, token);
  await room.startAudio();
  await room.localParticipant.setMicrophoneEnabled(true);
  return room;
}

export async function toggleMic(room) {
  const enabled = room.localParticipant.isMicrophoneEnabled;
  await room.localParticipant.setMicrophoneEnabled(!enabled);
  return !enabled;
}
