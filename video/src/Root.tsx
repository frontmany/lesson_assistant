import { Composition } from 'remotion';
import { Promo } from './Promo';
import { TOTAL } from './timeline';

export const Root: React.FC = () => (
  <Composition
    id="Promo"
    component={Promo}
    durationInFrames={TOTAL}
    fps={30}
    width={1920}
    height={1080}
  />
);
