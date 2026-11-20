const $=s=>document.querySelector(s);
const date=value=>value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false}):'—';
function status(message,error=false){$('#admin-status').textContent=message;$('#admin-status').dataset.error=String(error)}
async function api(path,body){const r=await fetch(path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(r.status===401){location.replace('/login?next=admin');throw new Error('请重新登录。')}const result=await r.json();if(!r.ok)throw new Error(result.error||'操作未完成');return result}
function node(tag,text,className){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n}
function table(host,heads,rows){host.replaceChildren();if(!rows.length){host.append(node('p','暂无记录','empty-list'));return}const t=node('table'),thead=node('thead'),tr=node('tr');heads.forEach(h=>tr.append(node('th',h)));thead.append(tr);t.append(thead);const body=node('tbody');rows.forEach(values=>{const row=node('tr');values.forEach(v=>{const td=node('td');td.append(v instanceof Node?v:document.createTextNode(String(v)));row.append(td)});body.append(row)});t.append(body);host.append(t)}
function button(text,fn){const b=node('button',text,'quiet-button');b.onclick=async()=>{b.disabled=true;try{await fn();await load();status('已保存。')}catch(e){status(e.message,true);b.disabled=false}};return b}
function twoLines(first,second){const n=node('span',first);n.append(node('small',second));return n}
async function load(){
 const data=await api('/api/admin');
 table($('#invite-list'),['邀请码 / 备注','使用','状态','操作'],data.invites.map(i=>{const expired=i.expires_at<=Date.now()/1000,unavailable=i.revoked||expired||i.uses>=i.max_uses;return [twoLines(i.hint,i.label),`${i.uses} / ${i.max_uses}`,twoLines(i.revoked?'已停用':expired?'已过期':i.uses>=i.max_uses?'已用完':'可使用',date(i.expires_at)+' 到期'),unavailable?'—':button('停用',()=>api('/api/admin',{action:'revoke_invite',id:i.id}))]}));
 table($('#user-list'),['成员','作品数','最近登录','状态','操作'],data.users.map(u=>{
   const control=u.role==='admin'?'—':button(u.disabled?'恢复账号':'停用账号',async()=>{
     if(!u.disabled&&!confirm(`停用 ${u.username}？该账号将退出登录，作品会保留。`))return;
     await api('/api/admin',{action:'set_user_disabled',user_id:u.id,disabled:!u.disabled});
   });
   return [twoLines(u.display_name,u.username+(u.role==='admin'?' · 管理员':'')),u.project_count,twoLines(date(u.last_login),u.last_ip||'—'),node('span',u.disabled?'已停用':'正常',`status-pill${u.disabled?' off':''}`),control];
 }));
 const names={login:'登录',register:'注册',password_changed:'修改密码',invite_created:'创建邀请码',invite_revoked:'停用邀请码',user_disabled:'停用账号',user_enabled:'恢复账号'};
 table($('#event-list'),['时间','账号','操作','IP / 备注'],data.events.map(e=>[date(e.created_at),e.username||'—',names[e.action]||e.action,twoLines(e.ip,e.detail)]));
}
$('#invite-form').onsubmit=async e=>{e.preventDefault();$('#create-invite').disabled=true;status('正在生成…');try{const result=await api('/api/admin',{action:'create_invite',label:$('#invite-label').value,max_uses:Number($('#invite-uses').value),days:Number($('#invite-days').value)});$('#invite-value').textContent=result.code;$('#invite-result').hidden=false;$('#copy-invite').textContent='复制邀请码';await load();status('邀请码已生成，可复制后发给新成员。')}catch(e){status(e.message,true)}finally{$('#create-invite').disabled=false}};
$('#copy-invite').onclick=async()=>{try{await navigator.clipboard.writeText($('#invite-value').textContent);$('#copy-invite').textContent='已复制'}catch{status('浏览器未允许复制，请选中邀请码手动复制。',true)}};
$('#logout').onclick=async()=>{try{await api('/api/auth',{action:'logout'});location.replace('/login')}catch(e){status(e.message,true)}};
addEventListener('pageshow',e=>{if(e.persisted)location.reload()});
load().catch(e=>status(e.message,true));
