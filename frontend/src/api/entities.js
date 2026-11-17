import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const blindReviews={
 list:()=>api('/blind-reviews'),
 get:id=>api('/blind-reviews/'+id),
 create:data=>api('/blind-reviews',{method:'POST',body:JSON.stringify(data)}),
 addCandidate:(id,data)=>api('/blind-reviews/'+id+'/candidates',{method:'POST',body:JSON.stringify(data)}),
 reveal:id=>api('/blind-reviews/'+id+'/reveal',{method:'POST'}),
 resume:id=>api('/blind-reviews/'+id+'/resume',{method:'POST'}),
 candidate:(id,cid)=>api('/blind-reviews/'+id+'/candidates/'+cid),
};
