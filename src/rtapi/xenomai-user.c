/********************************************************************
* Description:  xenomai-user.c
*               This file, 'xenomai-user.c', implements the unique
*               functions for the Xenomai userland thread system.
********************************************************************/

#include "config.h"
#include "rtapi.h"
#include "rtapi_common.h"

#include <sys/mman.h>		/* munlockall() */
#include <native/timer.h>	/* rt_timer_*() */

#ifdef RTAPI
//FIXME minimize
#include <native/mutex.h>
#include <rtdk.h>

#include <stdlib.h>		// abort()

#define MAX_ERRORS 3

/*  RTAPI task functions  */
RT_TASK ostask_array[RTAPI_MAX_TASKS + 1];

// this is needed due to the weirdness of the rt_task_self return value -
// it does _not_ match the address of the RT_TASK structure it was 
// created with
RT_TASK *ostask_self[RTAPI_MAX_TASKS + 1];
#endif  /* RTAPI */


/* init_rtapi_data */
void init_rtapi_data_hook(rtapi_data_t * data) {
    data->rt_wait_error = 0;
    data->rt_last_overrun = 0;
    data->rt_total_overruns = 0;
}


/* rtapi_init() and rtapi_exit() */

/***********************************************************************
*                    INIT AND EXIT FUNCTIONS                           *
************************************************************************/

int _rtapi_init(const char *modname) {

#ifdef ULAPI
    int retval;

    if ((rulapi_data == NULL) &&
	((retval = rulapi_data_attach(RULAPI_KEY, &rulapi_data)) < 0))
	return retval;
#endif

    return rtapi_next_module_id();
}

int _rtapi_exit(int module_id) {
  munlockall();
  return 0;
}


/***********************************************************************
*                           rtapi_task.c                               *
************************************************************************/

#ifdef RTAPI
int rtapi_task_delete_hook(task_data *task, int task_id) {
    int retval = 0;

    if ((retval = rt_task_delete( &ostask_array[task_id] )) < 0)
	rtapi_print_msg(RTAPI_MSG_ERR,"ERROR: rt_task_delete() = %d %s\n", 
			retval, strerror(retval));

    return retval;
}

void rtapi_task_wrapper(void * task_id_hack) {
    int ret;
    int task_id = (int)(long) task_id_hack; // ugly, but I ain't gonna fix it
    task_data *task = &task_array[task_id];

    /* use the argument to point to the task data */
    if (task->period < period) task->period = period;
    task->ratio = task->period / period;
    rtapi_print_msg(RTAPI_MSG_DBG,
		    "rtapi_task_wrapper: task %p '%s' period=%d "
		    "prio=%d ratio=%d\n",
		    task, task->name, task->ratio * period,
		    task->prio, task->ratio);

    ostask_self[task_id]  = rt_task_self();
    
    if ((ret = rt_task_set_periodic(NULL, TM_NOW, task->ratio * period)) < 0) {
	rtapi_print_msg(RTAPI_MSG_ERR,
			"ERROR: rt_task_set_periodic(%d,%s) failed %d\n", 
			task_id, task->name, ret);
	abort();
    }

    /* call the task function with the task argument */
    (task->taskcode) (task->arg);
    
    /* if the task ever returns, we record that fact */
    task->state = ENDED;
    rtapi_print_msg(RTAPI_MSG_ERR,
		    "ERROR: reached end of wrapper for task %d '%s'\n", 
		    task_id, task->name);
}


int rtapi_task_start_hook(task_data *task, int task_id) {
    int which_cpu = 0;
    int retval;

#if !defined(BROKEN_XENOMAU_CPU_AFFINITY)
    // seems to work for me
    // not sure T_CPU(n) is possible - see:
    // http://www.xenomai.org/pipermail/xenomai-help/2010-09/msg00081.html

    if (task->cpu > -1)  // explicitly set by threads, motmod
	which_cpu = T_CPU(task->cpu);
#endif

    // http://www.xenomai.org/documentation/trunk/html/api/group__task.html#ga03387550693c21d0223f739570ccd992
    // Passing T_FPU|T_CPU(1) in the mode parameter thus creates a
    // task with FPU support enabled and which will be affine to CPU #1
    // the task will start out dormant; execution begins with rt_task_start()

    // since this is a usermode RT task, it will be FP anyway
    if ((retval = rt_task_create (&ostask_array[task_id], task->name, 
				  task->stacksize, task->prio, 
				  (task->uses_fp ? T_FPU : 0) | which_cpu )
	 ) != 0) {
	rtapi_print_msg(RTAPI_MSG_ERR,
			"rt_task_create failed, rc = %d\n", retval );
	return -ENOMEM;
    }

    if ((retval = rt_task_start( &ostask_array[task_id],
				 rtapi_task_wrapper, (void *)(long)task_id))) {
	rtapi_print_msg(RTAPI_MSG_INFO,
			"rt_task_start failed, rc = %d\n", retval );
	return -ENOMEM;
    }
    return 0;
}

int rtapi_task_stop_hook(task_data *task, int task_id) {
    int retval;

    if ((retval = rt_task_delete( &ostask_array[task_id] )) < 0) {
	rtapi_print_msg(RTAPI_MSG_ERR,"rt_task_delete() = %d\n", retval);
	return retval;
    }

    return 0;
}

int rtapi_task_pause_hook(task_data *task, int task_id) {
    return rt_task_suspend( &ostask_array[task_id] );
}

int rtapi_task_resume_hook(task_data *task, int task_id) {
    return rt_task_resume( &ostask_array[task_id] );
}

void rtapi_wait_hook() {
    unsigned long overruns;
    static int error_printed = 0;
    int task_id;
    task_data *task;

    int result =  rt_task_wait_period(&overruns);
    switch (result) {
    case 0: // ok - no overruns;
	break;

    case -ETIMEDOUT: // release point was missed
	rtapi_data->rt_wait_error++;
	rtapi_data->rt_last_overrun = overruns;
	rtapi_data->rt_total_overruns += overruns;

	if (error_printed < MAX_ERRORS) {
	    task_id = rtapi_task_self();
	    task = &(task_array[task_id]);

	    rtapi_print_msg
		(RTAPI_MSG_ERR,
		 "RTAPI: ERROR: Unexpected realtime delay on task %d - "
		 "'%s' (%lu overruns)\n" 
		 "This Message will only display once per session.\n"
		 "Run the Latency Test and resolve before continuing.\n", 
		 task_id, task->name, overruns);
	}
	error_printed++;
	if(error_printed == MAX_ERRORS) 
	    rtapi_print_msg(RTAPI_MSG_ERR,
			    "RTAPI: (further messages will be suppressed)\n");
	break;

    case -EWOULDBLOCK:
	rtapi_print_msg(error_printed == 0 ? RTAPI_MSG_ERR : RTAPI_MSG_WARN,
			"RTAPI: ERROR: rt_task_wait_period() without "
			"previous rt_task_set_periodic()\n");
	error_printed++;
	break;

    case -EINTR:
	rtapi_print_msg(error_printed == 0 ? RTAPI_MSG_ERR : RTAPI_MSG_WARN,
			"RTAPI: ERROR: rt_task_unblock() called before "
			"release point\n");
	error_printed++;
	break;

    case -EPERM:
	rtapi_print_msg(error_printed == 0 ? RTAPI_MSG_ERR : RTAPI_MSG_WARN,
			"RTAPI: ERROR: cannot rt_task_wait_period() from "
			"this context\n");
	error_printed++;
	break;
    default:
	rtapi_print_msg(error_printed == 0 ? RTAPI_MSG_ERR : RTAPI_MSG_WARN,
			"RTAPI: ERROR: unknown error code %d\n", result);
	error_printed++;
	break;
    }
}

int rtapi_task_self_hook(void) {
    RT_TASK *ptr;
    int n;

    /* ask OS for pointer to its data for the current task */
    ptr = rt_task_self();

    if (ptr == NULL) {
	/* called from outside a task? */
	return -EINVAL;
    }
    /* find matching entry in task array */
    n = 1;
    while (n <= RTAPI_MAX_TASKS) {
	if (ostask_self[n] == ptr) {
	    /* found a match */
	    return n;
	}
	n++;
    }
    return -EINVAL;
}

#endif  /* RTAPI */


/***********************************************************************
*                           rtapi_time.c                               *
************************************************************************/

#ifdef RTAPI
void rtapi_delay_hook(long int nsec)
{
    long long int release = rt_timer_tsc() + nsec;
    while (rt_timer_tsc() < release);
}
#endif

long long int rtapi_get_time_hook(void) {
    /* The value returned will represent a count of jiffies if the
       native skin is bound to a periodic time base (see
       CONFIG_XENO_OPT_NATIVE_PERIOD), or nanoseconds otherwise.  */
    return rt_timer_read();
}

/* This returns a result in clocks instead of nS, and needs to be used
   with care around CPUs that change the clock speed to save power and
   other disgusting, non-realtime oriented behavior.  But at least it
   doesn't take a week every time you call it.
*/
long long int rtapi_get_clocks_hook(void) {
    // Gilles says: do this - it's portable
    return rt_timer_tsc();
}
