package com.zy.zhangyun;

import android.content.Context;
import android.graphics.*;
import android.util.AttributeSet;
import android.view.View;
import java.util.*;

public class CandleChartView extends View {
  public static class Candle { public String day; public double open, high, low, close; Candle(String d,double o,double h,double l,double c){day=d;open=o;high=h;low=l;close=c;} }
  private final Paint p=new Paint(Paint.ANTI_ALIAS_FLAG); private List<Candle> candles=new ArrayList<>();
  public CandleChartView(Context c, AttributeSet a){super(c,a);p.setTypeface(Typeface.create("sans",Typeface.NORMAL));setLayerType(View.LAYER_TYPE_SOFTWARE,null);}
  public void setCandles(List<Candle> value){candles=value==null?new ArrayList<>():value;invalidate();}
  protected void onDraw(Canvas c){super.onDraw(c);float w=getWidth(),h=getHeight(),padL=8,padR=56,padT=22,padB=30; p.setColor(Color.rgb(16,32,56));c.drawRoundRect(0,0,w,h,18,18,p);
    if(candles.isEmpty()){p.setColor(Color.rgb(145,164,189));p.setTextSize(14);p.setTextAlign(Paint.Align.CENTER);c.drawText("暂无图表数据",w/2,h/2,p);return;}
    double lo=Double.MAX_VALUE,hi=-Double.MAX_VALUE;for(Candle x:candles){lo=Math.min(lo,x.low);hi=Math.max(hi,x.high);}if(hi-lo<0.000001){hi+=.01;lo-=.01;}double margin=(hi-lo)*.12;hi+=margin;lo-=margin;
    p.setStrokeWidth(1);p.setColor(Color.rgb(39,60,86));for(int i=0;i<5;i++){float y=padT+(h-padT-padB)*i/4f;c.drawLine(padL,y,w-padR,y,p);p.setColor(Color.rgb(145,164,189));p.setTextSize(10);p.setTextAlign(Paint.Align.LEFT);c.drawText(String.format(Locale.US,"%.4f",hi-(hi-lo)*i/4),w-padR+7,y+4,p);p.setColor(Color.rgb(39,60,86));}
    float plotW=w-padL-padR, plotH=h-padT-padB, step=plotW/Math.max(candles.size(),2);float body=Math.max(3,Math.min(14,step*.56f));
    for(int i=0;i<candles.size();i++){Candle x=candles.get(i);float cx=padL+step*(i+.5f);float yh=(float)(padT+(hi-x.high)/(hi-lo)*plotH),yl=(float)(padT+(hi-x.low)/(hi-lo)*plotH),yo=(float)(padT+(hi-x.open)/(hi-lo)*plotH),yc=(float)(padT+(hi-x.close)/(hi-lo)*plotH);boolean up=x.close>=x.open;p.setColor(up?Color.rgb(215,25,32):Color.rgb(248,113,113));p.setStrokeWidth(1.4f);c.drawLine(cx,yh,cx,yl,p);c.drawRect(cx-body/2,Math.min(yo,yc),cx+body/2,Math.max(yo,yc)+1,p);}
    p.setTextAlign(Paint.Align.LEFT);p.setTextSize(10);p.setColor(Color.rgb(145,164,189));c.drawText(candles.get(0).day,padL,h-10,p);p.setTextAlign(Paint.Align.RIGHT);c.drawText(candles.get(candles.size()-1).day,w-padR,h-10,p);
  }
}
