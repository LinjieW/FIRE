/* FIRE Plan welcome: once per page load, after existing startup has finished.
   English-only brand copy is intentional. No storage, network, or plan actions. */
(function () {
  "use strict";
  let shown = false;
  window.WelcomeIntro = { open() {
    const root = document.getElementById("fire-intro");
    if (shown || !root || typeof root.showModal !== "function") return;
    shown = true;
    const q = selector => root.querySelector(selector);
    let animations = [];
    const reduced = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    function stop() { animations.forEach(a => a.cancel()); animations = []; }
function animate(selector,frames,delay,duration,easing='cubic-bezier(.2,.8,.2,1)'){const a=q(selector).animate(frames,{delay:delay*.6,duration:duration*.6,easing,fill:'both'});animations.push(a);}
function play(){animations.forEach(a=>a.cancel());animations=[];if(reduced && reduced.matches)return;const b=1;
try {
animate('.world',[{transform:'rotateX(24deg) rotateY(-32deg) scale(.82)'},{transform:'rotateX(12deg) rotateY(-12deg) scale(1)'}],0,3900);
animate('.sheet',[{opacity:0,transform:'translateY(-190px) rotateZ(32deg) scale(.25)'},{opacity:1,transform:`translateY(18px) rotateZ(-13deg) scale(${1+.05*b},${1-.06*b})`,offset:.6},{transform:'translateY(-9px) rotateZ(-6deg) scale(1)',offset:.8},{transform:'translateY(0) rotateZ(-9deg) scale(1)'}],0,1350);
animate('.back',[{opacity:0,transform:'rotateZ(-9deg) scale(.8)'},{opacity:1,transform:'rotateZ(21deg) translateZ(-25px)',offset:.75},{transform:'rotateZ(16deg) translateZ(-25px)'}],850,1300);
animate('.ring',[{opacity:0,transform:'translate(-170px,40px) rotateZ(-150deg) scale(.4)'},{opacity:1,transform:`translate(12px,-8px) rotateY(25deg) rotateX(25deg) scale(${1+.08*b})`,offset:.7},{transform:'translate(0,0) rotateY(25deg) rotateX(25deg) scale(1)'}],1100,1700);
animate('.cube',[{opacity:0,transform:'translate(160px,-100px) rotateZ(100deg) scale(.25)'},{opacity:1,transform:'translate(-10px,8px) rotateZ(14deg) rotateY(-15deg)',offset:.7},{transform:'translate(0,0) rotateZ(20deg) rotateY(-15deg)'}],1500,1500);
animate('.ball',[{opacity:0,transform:'translateY(-180px) scale(.5)'},{opacity:1,transform:'translateY(15px) scale(1.17,.84)',offset:.48},{transform:'translateY(-35px) scale(.96,1.04)',offset:.7},{transform:'translateY(0) scale(1)',offset:1}],2100,1600);
animate('.orbit',[{opacity:0,transform:'rotateZ(15deg) rotateX(53deg) scale(.4)'},{opacity:1,transform:'rotateZ(-26deg) rotateX(53deg) translateZ(-40px)'}],1500,2000);
animate('.route',[{opacity:0,transform:'translateY(12px)'},{opacity:1,transform:'translateY(0)'}],1600,900);
animate('.shadow',[{opacity:0,transform:'scale(.2)'},{opacity:1,transform:'scale(1)'}],300,2000);
animate('.title',[{opacity:0,transform:'translateY(25px) scale(.94)'},{opacity:1,transform:'translateY(-3px) scale(1.015)',offset:.75},{opacity:1,transform:'translateY(0) scale(1)'}],3350,850);
animate('.tagline',[{opacity:0,transform:'translateY(12px)'},{opacity:1,transform:'translateY(0)'}],3900,700);
animate('.start',[{transform:'scale(.94)'},{transform:'scale(1.06)',offset:.65},{transform:'scale(1)'}],4400,600);
} catch (_) { stop(); }
}

    const replay = () => { if (root.open) play(); };
    root.addEventListener("close", () => {
      stop();
      if (reduced && reduced.removeEventListener) reduced.removeEventListener("change", replay);
      const target = document.getElementById("startFresh");
      if (target) target.focus();
    }, { once: true });
    q(".replay").addEventListener("click", replay);
    try { root.showModal(); } catch (_) { return; }
    if (reduced && reduced.addEventListener) reduced.addEventListener("change", replay);
    play();
  }};
})();
