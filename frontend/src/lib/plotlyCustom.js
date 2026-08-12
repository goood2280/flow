/* plotly.js partial bundle — flow 가 실제로 쓰는 trace 만 등록한다.
 *
 * 예전에는 `plotly.js-dist-min`(전체 배포판)을 통째로 import 했다. 거기엔 3D
 * (surface/mesh), 지도(mapbox/maplibre), 재무(candlestick/ohlc), 극좌표, sankey 등
 * 40종 넘는 trace 가 들어 있는데 flow 는 아래 8종만 쓴다. 전체 배포판은 빌드
 * 산출물에서 4.88MB 를 차지했고, setup.py 번들이 origin/main 의 유일한 소스가 된
 * 뒤로는 그 크기가 그대로 배포 payload 가 된다.
 *
 * 쓰는 trace 를 늘릴 때는 여기에 import + register 를 추가해야 한다. 빠뜨리면
 * 화면에서 "trace type not found" 로 조용히 빈 차트가 나온다.
 *
 * scatter 는 core 에 기본 포함되어 있어 따로 등록하지 않는다.
 */
import Plotly from "plotly.js/lib/core";

import bar from "plotly.js/lib/bar";
import box from "plotly.js/lib/box";
import heatmap from "plotly.js/lib/heatmap";
import histogram from "plotly.js/lib/histogram";
import pie from "plotly.js/lib/pie";
import scattergl from "plotly.js/lib/scattergl";

Plotly.register([bar, box, heatmap, histogram, pie, scattergl]);

export default Plotly;
